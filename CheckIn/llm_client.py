"""Resilient, bounded access to language-model providers.

The SDK is configured with no hidden retries. This module owns model failover,
bulkheads, circuit breaking, deadlines, and safe operational counters so every
caller gets the same predictable behavior.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import re
import time
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from email.utils import parsedate_to_datetime
from typing import AsyncIterator, Literal

import anthropic
from anthropic import AsyncAnthropic

from config import (
    ANTHROPIC_API_KEY,
    ANTHROPIC_FALLBACK_MODEL,
    ANTHROPIC_FALLBACK_MODEL_2,
    ANTHROPIC_MODEL,
    LLM_CIRCUIT_COOLDOWN_SECONDS,
    LLM_CIRCUIT_FAILURE_THRESHOLD,
    LLM_GATEWAY_API_KEY,
    LLM_GATEWAY_BASE_URL,
    LLM_GATEWAY_ENABLED,
    LLM_MAX_CONCURRENT_CALLS,
    LLM_MAX_PAUSE_CONTINUATIONS,
    LLM_MAX_RESEARCH_CONCURRENT_CALLS,
    LLM_MAX_WEB_SEARCH_CALLS,
    LLM_QUEUE_TIMEOUT_SECONDS,
    LLM_RETRY_BASE_DELAY_SECONDS,
    LLM_RETRY_MAX_DELAY_SECONDS,
    LLM_TIMEOUT_SECONDS,
)

logger = logging.getLogger(__name__)

Workload = Literal["interactive", "research"]
Effort = Literal["low", "medium", "high", "xhigh", "max"]

# Anthropic's dynamic-filtering web search. Older tool versions exist but only
# for models this application does not target.
WEB_SEARCH_TOOL_TYPE = "web_search_20260209"

# Adaptive thinking spends part of `max_tokens` before any visible text is
# produced. A caller asking for a two-sentence answer must still leave room for
# that, or the turn stops at the cap with an empty text block.
MIN_MAX_TOKENS = 1024

# Appended to the system prompt when a caller needs machine-readable output.
# Anthropic's structured outputs enforce a full JSON Schema; the schemas here
# carry defaults our own ranker owns (rank, score) and tolerate partially
# invalid candidate lists, so the contract stays prompt-level and the callers
# keep their existing repair attempt.
JSON_ONLY_INSTRUCTION = (
    "\n\nRespond with a single valid JSON value and nothing else. Do not add "
    "explanations, preambles, or markdown code fences."
)


class LLMError(RuntimeError):
    """Base exception with a stable internal failure code."""

    def __init__(self, message: str, *, code: str, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class LLMUnavailableError(LLMError):
    def __init__(self, message: str = "No language model is currently available") -> None:
        super().__init__(message, code="MODELS_UNAVAILABLE", retryable=True)


class LLMCapacityError(LLMError):
    def __init__(self) -> None:
        super().__init__(
            "Language-model capacity is temporarily busy",
            code="CAPACITY_TIMEOUT",
            retryable=True,
        )


class LLMInvalidResponseError(LLMError):
    def __init__(self) -> None:
        super().__init__(
            "The language model returned an empty response",
            code="INVALID_OUTPUT",
            retryable=True,
        )


class LLMRefusalError(LLMError):
    """The model declined the request under its own safety policy.

    Another configured model may still answer, so this is retryable in the same
    sense as a provider error: it is a verdict about one model, not the account.
    """

    def __init__(self) -> None:
        super().__init__(
            "The language model declined to answer this request",
            code="REFUSAL",
            retryable=True,
        )


class LLMPauseLimitError(LLMError):
    """A server-side tool loop never finished inside its continuation budget."""

    def __init__(self) -> None:
        super().__init__(
            "The language model did not finish its research within the allowed steps",
            code="PAUSE_LIMIT",
            retryable=True,
        )


@dataclass
class _CircuitState:
    consecutive_failures: int = 0
    opened_until: float = 0.0
    half_open_probe: bool = False

    def allow_call(self, now: float) -> bool:
        if self.opened_until == 0:
            return True
        if now < self.opened_until:
            return False
        # Exactly one request probes a model after the cooldown. Because this
        # method has no await, this transition is atomic within an event loop.
        if self.half_open_probe:
            return False
        self.half_open_probe = True
        return True

    def success(self) -> None:
        self.consecutive_failures = 0
        self.opened_until = 0.0
        self.half_open_probe = False

    def failure(self, now: float) -> None:
        self.consecutive_failures += 1
        if (
            self.half_open_probe
            or self.consecutive_failures >= LLM_CIRCUIT_FAILURE_THRESHOLD
        ):
            self.opened_until = now + LLM_CIRCUIT_COOLDOWN_SECONDS
        self.half_open_probe = False

    def abandon_probe(self, now: float, *, reopen: bool) -> None:
        """Release a half-open probe that never produced a usable verdict."""
        if not self.half_open_probe:
            return
        self.half_open_probe = False
        self.opened_until = now + LLM_CIRCUIT_COOLDOWN_SECONDS if reopen else now

    def status(self, now: float) -> str:
        if self.opened_until == 0:
            return "closed"
        if now < self.opened_until:
            return "open"
        return "half_open"


@dataclass
class _ModelMetrics:
    attempts: int = 0
    successes: int = 0
    failures: int = 0
    failover_attempts: int = 0
    failover_successes: int = 0
    short_circuits: int = 0
    pause_continuations: int = 0
    refusals: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    total_latency_ms: float = 0.0
    in_flight: int = 0


# The SDK's default retries previously multiplied with model and agent retries.
# Keep it at zero and own the complete retry budget here. The gateway (when
# enabled) is configured with num_retries=0 as well, so this module remains the
# single owner of retries across app, SDK, and gateway.
def _build_client() -> AsyncAnthropic:
    """Build the native Anthropic transport for the active topology.

    Gateway mode points the same SDK at the LiteLLM Anthropic passthrough
    prefix using a gateway virtual key; the provider key then lives only in
    the gateway. Direct mode is the kill switch: it talks to
    api.anthropic.com with the app-side resilience unchanged.
    """
    if LLM_GATEWAY_ENABLED:
        return AsyncAnthropic(
            api_key=LLM_GATEWAY_API_KEY,
            base_url=LLM_GATEWAY_BASE_URL,
            timeout=LLM_TIMEOUT_SECONDS,
            max_retries=0,
        )
    return AsyncAnthropic(
        api_key=ANTHROPIC_API_KEY,
        timeout=LLM_TIMEOUT_SECONDS,
        max_retries=0,
    )


_client = _build_client()


def _rebuild_client() -> None:
    """Rebuild the SDK client after configuration changes (used by tests)."""
    global _client
    _client = _build_client()
_call_limiter = asyncio.Semaphore(LLM_MAX_CONCURRENT_CALLS)
_research_limiter = asyncio.Semaphore(LLM_MAX_RESEARCH_CONCURRENT_CALLS)
_circuits: dict[str, _CircuitState] = {}
_metrics: dict[str, _ModelMetrics] = {}
_queue_timeouts = 0
_account_blocked_code: str | None = None


def _circuit(model: str) -> _CircuitState:
    return _circuits.setdefault(model, _CircuitState())


def _model_metrics(model: str) -> _ModelMetrics:
    return _metrics.setdefault(model, _ModelMetrics())


def _error_body_text(exc: Exception) -> str:
    """Read stable SDK error metadata without using it in logs or responses."""
    body = getattr(exc, "body", None)
    if body is None:
        return ""
    try:
        return json.dumps(body, sort_keys=True).lower()
    except (TypeError, ValueError):
        return str(body).lower()


def _is_quota_error(exc: Exception) -> bool:
    if getattr(exc, "type", None) == "billing_error":
        return True
    body = _error_body_text(exc)
    return "credit balance" in body or "billing_error" in body


def _is_account_blocking_error(exc: Exception) -> bool:
    return isinstance(exc, anthropic.AuthenticationError) or _is_quota_error(exc)


def is_retryable_error(exc: Exception) -> bool:
    """Return whether another configured model may safely be attempted."""
    if isinstance(exc, LLMError):
        return exc.retryable
    if isinstance(exc, anthropic.RateLimitError):
        return not _is_quota_error(exc)
    if isinstance(exc, (anthropic.PermissionDeniedError, anthropic.NotFoundError)):
        # Access and model availability can differ between configured models.
        return not _is_quota_error(exc)
    if isinstance(
        exc,
        (
            anthropic.APIConnectionError,
            anthropic.APITimeoutError,
            anthropic.InternalServerError,
        ),
    ):
        return True
    if isinstance(exc, anthropic.APIStatusError):
        return exc.status_code in {408, 409, 429} or exc.status_code >= 500
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return True

    # Some tests and future provider adapters may raise ordinary exceptions.
    # Keep this narrow; authorization and arbitrary programming errors must not
    # be retried based on loose message matching.
    text = str(exc).lower()
    return "timed out" in text or "timeout" in text or "temporarily unavailable" in text


def is_fatal_error(exc: Exception) -> bool:
    """Return whether retrying with another model on this account cannot help."""
    if isinstance(exc, LLMError):
        return not exc.retryable
    if _is_quota_error(exc):
        return True
    return isinstance(
        exc,
        (
            anthropic.AuthenticationError,
            anthropic.BadRequestError,
            anthropic.UnprocessableEntityError,
        ),
    )


def _error_code(exc: Exception) -> str:
    if isinstance(exc, LLMError):
        return exc.code
    if _is_quota_error(exc):
        return "QUOTA"
    if isinstance(exc, anthropic.AuthenticationError):
        return "AUTH"
    if isinstance(exc, anthropic.PermissionDeniedError):
        return "PERMISSION"
    if isinstance(exc, anthropic.RateLimitError):
        return "RATE_LIMIT"
    if isinstance(exc, (anthropic.APITimeoutError, TimeoutError, asyncio.TimeoutError)):
        return "TIMEOUT"
    if isinstance(exc, anthropic.APIConnectionError):
        return "NETWORK"
    if isinstance(exc, anthropic.APIStatusError) and exc.status_code >= 500:
        return "PROVIDER_5XX"
    if isinstance(exc, anthropic.APIStatusError):
        return f"HTTP_{exc.status_code}"
    return "UNKNOWN"


def _retry_after_seconds(exc: Exception) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    value = headers.get("retry-after")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        try:
            retry_at = parsedate_to_datetime(value).timestamp()
            return max(0.0, retry_at - time.time())
        except (TypeError, ValueError, OverflowError):
            return None


def _backoff_seconds(exc: Exception, attempt_index: int) -> float:
    retry_after = _retry_after_seconds(exc)
    if retry_after is not None:
        return min(retry_after, LLM_RETRY_MAX_DELAY_SECONDS)
    cap = min(
        LLM_RETRY_MAX_DELAY_SECONDS,
        LLM_RETRY_BASE_DELAY_SECONDS * (2**attempt_index),
    )
    return random.uniform(0, max(0.0, cap))


@asynccontextmanager
async def _capacity(workload: Workload) -> AsyncIterator[None]:
    """Acquire the global bulkhead and, for searches, the research bulkhead."""
    global _queue_timeouts
    if LLM_GATEWAY_ENABLED:
        # The gateway enforces the real cross-worker concurrency cap and rate
        # limits on its virtual keys. Running the per-process semaphores as
        # well would double-limit (they multiply by worker count) and mask the
        # gateway's own admission decisions, so admission control moves there.
        yield
        return
    acquired: list[asyncio.Semaphore] = []
    semaphores = [_call_limiter]
    if workload == "research":
        semaphores.insert(0, _research_limiter)

    try:
        try:
            async with asyncio.timeout(LLM_QUEUE_TIMEOUT_SECONDS):
                for semaphore in semaphores:
                    await semaphore.acquire()
                    acquired.append(semaphore)
        except TimeoutError as exc:
            _queue_timeouts += 1
            raise LLMCapacityError() from exc
        yield
    finally:
        for semaphore in reversed(acquired):
            semaphore.release()


def _ordered_models(cheap: bool, prefer_fallback: bool) -> list[str]:
    fallbacks = [
        model
        for model in (ANTHROPIC_FALLBACK_MODEL, ANTHROPIC_FALLBACK_MODEL_2)
        if model
    ]
    if cheap or prefer_fallback:
        order = [*fallbacks, ANTHROPIC_MODEL]
    else:
        order = [ANTHROPIC_MODEL, *fallbacks]
    return list(dict.fromkeys(model for model in order if model))


def _record_usage(metrics: _ModelMetrics, response: object) -> None:
    usage = getattr(response, "usage", None)
    metrics.input_tokens += int(getattr(usage, "input_tokens", 0) or 0)
    metrics.output_tokens += int(getattr(usage, "output_tokens", 0) or 0)
    metrics.cache_read_tokens += int(
        getattr(usage, "cache_read_input_tokens", 0) or 0
    )


def _response_text(response: object) -> str:
    """Concatenate the visible text blocks of one message.

    Thinking, server-tool calls, and search results are deliberately dropped:
    only `text` blocks carry the answer, and thinking is never displayed.
    """
    blocks = getattr(response, "content", None) or []
    parts = [
        block.text
        for block in blocks
        if getattr(block, "type", None) == "text" and getattr(block, "text", None)
    ]
    return "".join(parts).strip()


def _refusal_category(response: object) -> str:
    details = getattr(response, "stop_details", None)
    return str(getattr(details, "category", None) or "unspecified")


async def generate_text(
    prompt: str,
    system_instruction: str | None = None,
    use_search: bool = False,
    max_tokens: int = 16000,
    cheap: bool = False,
    *,
    prefer_fallback: bool = False,
    json_mode: bool = False,
    effort: Effort = "medium",
    workload: Workload = "interactive",
    operation: str = "generation",
    return_meta: bool = False,
) -> str | tuple[str, dict]:
    """Generate text with a strict per-model budget and circuit-aware failover.

    Each configured model (primary plus the ordered fallbacks) receives at
    most one logical turn. Resuming a paused server-side search loop stays
    inside that turn and inside the per-call deadline; neither the SDK nor the
    gateway may add hidden retries — this loop owns the whole budget. Callers
    may perform a separate schema-repair attempt, but higher-level wall
    deadlines still cap the complete operation.

    With ``return_meta=True`` the result is ``(text, {"model", "failover"})``
    so callers such as the research cache can record which model answered.

    Sampling temperature is not a parameter: the models this gateway targets
    reject it. Use `effort` to trade depth against cost and latency instead.
    """
    global _account_blocked_code
    tools = (
        [
            {
                "type": WEB_SEARCH_TOOL_TYPE,
                "name": "web_search",
                "max_uses": LLM_MAX_WEB_SEARCH_CALLS,
            }
        ]
        if use_search
        else None
    )
    system = system_instruction or ""
    if json_mode:
        system = (system + JSON_ONLY_INSTRUCTION).strip()

    # PII posture: the application never logs prompt or profile text. A stable
    # hash correlates app log lines with gateway records without capture.
    prompt_sha = hashlib.sha256(
        (system + "\x00" + prompt).encode("utf-8")
    ).hexdigest()[:16]

    models = _ordered_models(cheap, prefer_fallback)
    last_error: Exception | None = None

    for index, model in enumerate(models):
        circuit = _circuit(model)
        metrics = _model_metrics(model)
        now = time.monotonic()
        if not circuit.allow_call(now):
            metrics.short_circuits += 1
            logger.warning(
                "LLM call skipped operation=%s model=%s reason=circuit_open",
                operation,
                model,
            )
            continue

        owns_half_open_probe = circuit.half_open_probe
        provider_started = False
        started = time.monotonic()

        try:
            kwargs: dict = {
                "model": model,
                "max_tokens": max(max_tokens, MIN_MAX_TOKENS),
                "thinking": {"type": "adaptive"},
                "output_config": {"effort": effort},
            }
            if system:
                kwargs["system"] = system
            if tools:
                kwargs["tools"] = tools

            async with _capacity(workload):
                started = time.monotonic()
                provider_started = True
                metrics.attempts += 1
                metrics.in_flight += 1
                if index > 0:
                    metrics.failover_attempts += 1
                async with asyncio.timeout(LLM_TIMEOUT_SECONDS):
                    result = await _complete_turn(
                        kwargs, prompt, metrics, operation=operation, model=model
                    )

            metrics.successes += 1
            if index > 0:
                metrics.failover_successes += 1
            circuit.success()
            _account_blocked_code = None
            logger.info(
                "LLM call succeeded operation=%s model=%s failover=%s "
                "latency_ms=%.0f prompt_sha=%s",
                operation,
                model,
                index > 0,
                (time.monotonic() - started) * 1000,
                prompt_sha,
            )
            if return_meta:
                return result, {"model": model, "failover": index > 0}
            return result
        except LLMCapacityError:
            # Local admission pressure says nothing about provider health and
            # trying another model would hit the same full bulkhead.
            circuit.abandon_probe(time.monotonic(), reopen=False)
            logger.warning("LLM capacity timeout operation=%s", operation)
            raise
        except asyncio.CancelledError:
            circuit.abandon_probe(
                time.monotonic(),
                reopen=provider_started,
            )
            raise
        except Exception as exc:
            metrics.failures += 1
            last_error = exc
            retryable = is_retryable_error(exc)
            if retryable:
                circuit.failure(time.monotonic())
            elif owns_half_open_probe:
                circuit.abandon_probe(time.monotonic(), reopen=True)
            if _is_account_blocking_error(exc):
                _account_blocked_code = _error_code(exc)
            logger.warning(
                "LLM call failed operation=%s model=%s code=%s retryable=%s "
                "prompt_sha=%s",
                operation,
                model,
                _error_code(exc),
                retryable,
                prompt_sha,
            )
            if is_fatal_error(exc) or not retryable:
                raise
            if index + 1 < len(models):
                await asyncio.sleep(_backoff_seconds(exc, index))
        finally:
            if provider_started:
                metrics.in_flight -= 1
                metrics.total_latency_ms += (time.monotonic() - started) * 1000

    raise LLMUnavailableError() from last_error


async def _complete_turn(
    kwargs: dict,
    prompt: str,
    metrics: _ModelMetrics,
    *,
    operation: str,
    model: str,
) -> str:
    """Run one logical turn, resuming a paused server-side tool loop.

    A web search that exhausts the server's own iteration limit returns
    `stop_reason="pause_turn"`. Re-sending the conversation with the paused
    assistant turn appended resumes it; no extra user message is added because
    the API detects the trailing server-tool block itself.
    """
    messages: list[dict] = [{"role": "user", "content": prompt}]
    collected: list[str] = []

    for continuation in range(LLM_MAX_PAUSE_CONTINUATIONS + 1):
        response = await _client.messages.create(**kwargs, messages=messages)
        _record_usage(metrics, response)

        if response.stop_reason == "refusal":
            metrics.refusals += 1
            logger.warning(
                "LLM refused operation=%s model=%s category=%s",
                operation,
                model,
                _refusal_category(response),
            )
            raise LLMRefusalError()

        text = _response_text(response)
        if text:
            collected.append(text)

        if response.stop_reason != "pause_turn":
            if response.stop_reason == "max_tokens":
                logger.warning(
                    "LLM output truncated at max_tokens operation=%s model=%s",
                    operation,
                    model,
                )
            break

        if continuation >= LLM_MAX_PAUSE_CONTINUATIONS:
            logger.warning(
                "LLM turn still paused after %d continuations operation=%s model=%s",
                LLM_MAX_PAUSE_CONTINUATIONS,
                operation,
                model,
            )
            raise LLMPauseLimitError()

        metrics.pause_continuations += 1
        messages = messages + [{"role": "assistant", "content": response.content}]

    result = "\n".join(collected).strip()
    if not result:
        raise LLMInvalidResponseError()
    return result


def get_llm_health() -> dict:
    """Return a prompt-free, credential-free operational snapshot."""
    now = time.monotonic()
    routes = {}
    role_models = [
        ("primary", ANTHROPIC_MODEL),
        ("fallback", ANTHROPIC_FALLBACK_MODEL),
    ]
    if ANTHROPIC_FALLBACK_MODEL_2:
        role_models.append(("fallback_2", ANTHROPIC_FALLBACK_MODEL_2))
    for role, model in role_models:
        metrics = _model_metrics(model)
        circuit = _circuit(model)
        payload = asdict(metrics)
        payload["average_latency_ms"] = round(
            metrics.total_latency_ms / metrics.attempts, 1
        ) if metrics.attempts else 0.0
        payload.pop("total_latency_ms")
        payload["circuit"] = circuit.status(now)
        payload["consecutive_failures"] = circuit.consecutive_failures
        routes[role] = payload

    states = [payload["circuit"] for payload in routes.values()]
    if _account_blocked_code:
        status = "unavailable"
    elif states and all(state == "open" for state in states):
        status = "unavailable"
    elif any(state != "closed" for state in states):
        status = "degraded"
    else:
        status = "ok"
    # Local import: the cache pulls in the database layer, which this module
    # must not require at import time (tests import llm_client in isolation).
    try:
        from llm_cache import get_cache_stats

        cache_stats = get_cache_stats()
    except Exception:  # pragma: no cover - cache stats are best-effort
        cache_stats = None

    return {
        "status": status,
        "account": {
            "status": "blocked" if _account_blocked_code else "ready",
            "code": _account_blocked_code,
        },
        "gateway": {
            "enabled": LLM_GATEWAY_ENABLED,
            "mode": "anthropic_passthrough" if LLM_GATEWAY_ENABLED else "direct",
        },
        "research_cache": cache_stats,
        "queue_timeouts": _queue_timeouts,
        "routes": routes,
    }


def _reset_resilience_state() -> None:
    """Reset process-local counters and circuits (used by isolated tests)."""
    global _account_blocked_code, _queue_timeouts
    _circuits.clear()
    _metrics.clear()
    _queue_timeouts = 0
    _account_blocked_code = None


_JSON_SPAN = re.compile(r"[{\[].*[}\]]", re.DOTALL)


def parse_json_text(raw_text: str):
    """json.loads but tolerant of fences and of a short lead-in sentence.

    A research turn that used web search often narrates before answering, so a
    bare ``json.loads`` is not enough on its own.
    """
    text = raw_text.strip()
    if text.startswith("```"):
        if "\n" in text:
            text = text.split("\n", 1)[1]
        else:
            text = text[3:]
    if text.endswith("```"):
        text = text[:-3].strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = _JSON_SPAN.search(text)
        if match is None:
            raise
        return json.loads(match.group(0))
