"""Resilient, bounded access to language-model providers.

The SDK is configured with no hidden retries. This module owns model failover,
bulkheads, circuit breaking, deadlines, and safe operational counters so every
caller gets the same predictable behavior.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from email.utils import parsedate_to_datetime
from typing import AsyncIterator, Literal

import openai
from openai import AsyncOpenAI

from config import (
    LLM_CIRCUIT_COOLDOWN_SECONDS,
    LLM_CIRCUIT_FAILURE_THRESHOLD,
    LLM_MAX_CONCURRENT_CALLS,
    LLM_MAX_RESEARCH_CONCURRENT_CALLS,
    LLM_MAX_WEB_SEARCH_CALLS,
    LLM_QUEUE_TIMEOUT_SECONDS,
    LLM_RETRY_BASE_DELAY_SECONDS,
    LLM_RETRY_MAX_DELAY_SECONDS,
    LLM_TIMEOUT_SECONDS,
    OPENAI_API_KEY,
    OPENAI_FALLBACK_MODEL,
    OPENAI_MODEL,
)

logger = logging.getLogger(__name__)

Workload = Literal["interactive", "research"]


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
    input_tokens: int = 0
    output_tokens: int = 0
    total_latency_ms: float = 0.0
    in_flight: int = 0


# The SDK's default retries previously multiplied with model and agent retries.
# Keep it at zero and own the complete retry budget here.
_client = AsyncOpenAI(
    api_key=OPENAI_API_KEY,
    timeout=LLM_TIMEOUT_SECONDS,
    max_retries=0,
)
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
    body = _error_body_text(exc)
    return "insufficient_quota" in body or "billing_hard_limit_reached" in body


def _is_account_blocking_error(exc: Exception) -> bool:
    return isinstance(exc, openai.AuthenticationError) or _is_quota_error(exc)


def is_retryable_error(exc: Exception) -> bool:
    """Return whether another configured model may safely be attempted."""
    if isinstance(exc, LLMError):
        return exc.retryable
    if isinstance(exc, openai.RateLimitError):
        return not _is_quota_error(exc)
    if isinstance(exc, (openai.PermissionDeniedError, openai.NotFoundError)):
        # Access and model availability can differ between configured models.
        return True
    if isinstance(
        exc,
        (
            openai.APIConnectionError,
            openai.APITimeoutError,
            openai.InternalServerError,
        ),
    ):
        return True
    if isinstance(exc, openai.APIStatusError):
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
            openai.AuthenticationError,
            openai.BadRequestError,
            openai.UnprocessableEntityError,
        ),
    )


def _error_code(exc: Exception) -> str:
    if isinstance(exc, LLMError):
        return exc.code
    if _is_quota_error(exc):
        return "QUOTA"
    if isinstance(exc, openai.AuthenticationError):
        return "AUTH"
    if isinstance(exc, openai.PermissionDeniedError):
        return "PERMISSION"
    if isinstance(exc, openai.RateLimitError):
        return "RATE_LIMIT"
    if isinstance(exc, (openai.APITimeoutError, TimeoutError, asyncio.TimeoutError)):
        return "TIMEOUT"
    if isinstance(exc, openai.APIConnectionError):
        return "NETWORK"
    if isinstance(exc, openai.APIStatusError) and exc.status_code >= 500:
        return "PROVIDER_5XX"
    if isinstance(exc, openai.APIStatusError):
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
    if cheap or prefer_fallback:
        order = [OPENAI_FALLBACK_MODEL, OPENAI_MODEL]
    else:
        order = [OPENAI_MODEL, OPENAI_FALLBACK_MODEL]
    return list(dict.fromkeys(model for model in order if model))


def _record_usage(metrics: _ModelMetrics, response: object) -> None:
    usage = getattr(response, "usage", None)
    metrics.input_tokens += int(getattr(usage, "input_tokens", 0) or 0)
    metrics.output_tokens += int(getattr(usage, "output_tokens", 0) or 0)


async def generate_text(
    prompt: str,
    system_instruction: str | None = None,
    use_search: bool = False,
    max_output_tokens: int = 4096,
    temperature: float = 0.3,
    cheap: bool = False,
    *,
    prefer_fallback: bool = False,
    json_mode: bool = False,
    workload: Workload = "interactive",
    operation: str = "generation",
) -> str:
    """Generate text with a strict two-model budget and circuit-aware failover.

    Each configured model receives at most one actual provider call. The SDK
    cannot add hidden retries. Callers may perform a separate schema-repair
    attempt, but higher-level wall deadlines still cap the complete operation.
    """
    global _account_blocked_code
    tools = [{"type": "web_search"}] if use_search else None
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
                "input": prompt,
                "max_output_tokens": max_output_tokens,
                "temperature": temperature,
            }
            if system_instruction:
                kwargs["instructions"] = system_instruction
            if tools:
                kwargs["tools"] = tools
                kwargs["max_tool_calls"] = LLM_MAX_WEB_SEARCH_CALLS
            if json_mode:
                kwargs["text"] = {"format": {"type": "json_object"}}

            async with _capacity(workload):
                started = time.monotonic()
                provider_started = True
                metrics.attempts += 1
                metrics.in_flight += 1
                if index > 0:
                    metrics.failover_attempts += 1
                async with asyncio.timeout(LLM_TIMEOUT_SECONDS):
                    response = await _client.responses.create(**kwargs)
            result = response.output_text
            if not result:
                raise LLMInvalidResponseError()

            metrics.successes += 1
            if index > 0:
                metrics.failover_successes += 1
            _record_usage(metrics, response)
            circuit.success()
            _account_blocked_code = None
            logger.info(
                "LLM call succeeded operation=%s model=%s failover=%s latency_ms=%.0f",
                operation,
                model,
                index > 0,
                (time.monotonic() - started) * 1000,
            )
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
                "LLM call failed operation=%s model=%s code=%s retryable=%s",
                operation,
                model,
                _error_code(exc),
                retryable,
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


def get_llm_health() -> dict:
    """Return a prompt-free, credential-free operational snapshot."""
    now = time.monotonic()
    routes = {}
    for role, model in (
        ("primary", OPENAI_MODEL),
        ("fallback", OPENAI_FALLBACK_MODEL),
    ):
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
    return {
        "status": status,
        "account": {
            "status": "blocked" if _account_blocked_code else "ready",
            "code": _account_blocked_code,
        },
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


def parse_json_text(raw_text: str):
    """json.loads but tolerant of legacy ```json fences around a response."""
    text = raw_text.strip()
    if text.startswith("```"):
        if "\n" in text:
            text = text.split("\n", 1)[1]
        else:
            text = text[3:]
    if text.endswith("```"):
        text = text[:-3].strip()
    return json.loads(text)
