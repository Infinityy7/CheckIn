"""Bounded failover, circuit breaking, and output-mode behavior."""

from __future__ import annotations

import asyncio
import time
from contextlib import suppress
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

import llm_client


def _text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def _response(
    text: str = "ok",
    *,
    stop_reason: str = "end_turn",
    stop_details: object | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        content=[_text_block(text)] if text else [],
        stop_reason=stop_reason,
        stop_details=stop_details,
        usage=SimpleNamespace(
            input_tokens=10, output_tokens=4, cache_read_input_tokens=0
        ),
    )


def _timeout() -> anthropic.APITimeoutError:
    return anthropic.APITimeoutError(
        request=httpx2.Request("POST", "https://api.test")
    )


@pytest.fixture(autouse=True)
def direct_topology():
    """Bulkhead and deadline assertions assume the direct path; force it."""
    mp = pytest.MonkeyPatch()
    mp.setattr(llm_client, "LLM_GATEWAY_ENABLED", False)
    mp.setattr(llm_client, "LLM_GATEWAY_API_KEY", "")
    llm_client._rebuild_client()
    try:
        yield
    finally:
        mp.undo()
        llm_client._rebuild_client()


@pytest.fixture(autouse=True)
def reset_resilience(monkeypatch):
    llm_client._reset_resilience_state()
    monkeypatch.setattr(llm_client, "LLM_RETRY_BASE_DELAY_SECONDS", 0)
    monkeypatch.setattr(llm_client, "LLM_RETRY_MAX_DELAY_SECONDS", 0)
    yield
    llm_client._reset_resilience_state()


def test_primary_timeout_falls_back_once_and_requests_json(monkeypatch):
    calls: list[dict] = []

    async def create(**kwargs):
        calls.append(kwargs)
        if kwargs["model"] == llm_client.ANTHROPIC_MODEL:
            raise _timeout()
        return _response('{"recommendations": []}')

    monkeypatch.setattr(llm_client._client.messages, "create", create)
    result = asyncio.run(
        llm_client.generate_text("hello", json_mode=True, use_search=True)
    )

    assert result == '{"recommendations": []}'
    assert [call["model"] for call in calls] == [
        llm_client.ANTHROPIC_MODEL,
        llm_client.ANTHROPIC_FALLBACK_MODEL,
    ]
    fallback = calls[1]
    assert fallback["messages"] == [{"role": "user", "content": "hello"}]
    assert fallback["system"].endswith("markdown code fences.")
    assert fallback["thinking"] == {"type": "adaptive"}
    assert fallback["tools"] == [
        {
            "type": llm_client.WEB_SEARCH_TOOL_TYPE,
            "name": "web_search",
            "max_uses": llm_client.LLM_MAX_WEB_SEARCH_CALLS,
        }
    ]
    # The models this gateway targets reject a sampling temperature outright.
    assert "temperature" not in fallback


def test_effort_and_token_floor_are_forwarded(monkeypatch):
    calls: list[dict] = []

    async def create(**kwargs):
        calls.append(kwargs)
        return _response()

    monkeypatch.setattr(llm_client._client.messages, "create", create)
    asyncio.run(llm_client.generate_text("hello", max_tokens=16, effort="high"))

    assert calls[0]["output_config"] == {"effort": "high"}
    # Adaptive thinking spends part of the cap before any visible text.
    assert calls[0]["max_tokens"] == llm_client.MIN_MAX_TOKENS


def test_paused_search_loop_resumes_within_its_budget(monkeypatch):
    calls: list[dict] = []

    async def create(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return _response("searching...", stop_reason="pause_turn")
        return _response('{"recommendations": []}')

    monkeypatch.setattr(llm_client._client.messages, "create", create)
    result = asyncio.run(llm_client.generate_text("hello", use_search=True))

    assert result == 'searching...\n{"recommendations": []}'
    # One logical turn: both calls went to the primary, and no failover ran.
    assert [call["model"] for call in calls] == [llm_client.ANTHROPIC_MODEL] * 2
    assert calls[1]["messages"][-1]["role"] == "assistant"
    primary = llm_client.get_llm_health()["routes"]["primary"]
    assert primary["attempts"] == 1
    assert primary["pause_continuations"] == 1


def test_endlessly_paused_turn_fails_over_instead_of_hanging(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(llm_client, "LLM_MAX_PAUSE_CONTINUATIONS", 1)

    async def create(**kwargs):
        calls.append(kwargs["model"])
        if kwargs["model"] == llm_client.ANTHROPIC_MODEL:
            return _response("still working", stop_reason="pause_turn")
        return _response("fallback finished")

    monkeypatch.setattr(llm_client._client.messages, "create", create)
    assert asyncio.run(llm_client.generate_text("hello")) == "fallback finished"
    assert calls == [
        llm_client.ANTHROPIC_MODEL,
        llm_client.ANTHROPIC_MODEL,
        llm_client.ANTHROPIC_FALLBACK_MODEL,
    ]


def test_refusal_fails_over_and_never_leaks_the_explanation(monkeypatch):
    calls: list[str] = []

    async def create(**kwargs):
        calls.append(kwargs["model"])
        if kwargs["model"] == llm_client.ANTHROPIC_MODEL:
            return _response(
                "",
                stop_reason="refusal",
                stop_details=SimpleNamespace(
                    type="refusal",
                    category="cyber",
                    explanation="private explanation text",
                ),
            )
        return _response("fallback answered")

    monkeypatch.setattr(llm_client._client.messages, "create", create)
    assert asyncio.run(llm_client.generate_text("hello")) == "fallback answered"
    assert calls == [
        llm_client.ANTHROPIC_MODEL,
        llm_client.ANTHROPIC_FALLBACK_MODEL,
    ]
    snapshot = llm_client.get_llm_health()
    assert snapshot["routes"]["primary"]["refusals"] == 1
    assert "private explanation" not in str(snapshot)


def test_open_circuit_skips_primary_then_allows_one_probe(monkeypatch):
    calls: list[str] = []
    primary_failures = [True]
    monkeypatch.setattr(llm_client, "LLM_CIRCUIT_FAILURE_THRESHOLD", 1)
    monkeypatch.setattr(llm_client, "LLM_CIRCUIT_COOLDOWN_SECONDS", 0.01)

    async def create(**kwargs):
        model = kwargs["model"]
        calls.append(model)
        if model == llm_client.ANTHROPIC_MODEL and primary_failures:
            primary_failures.pop()
            raise _timeout()
        return _response()

    monkeypatch.setattr(llm_client._client.messages, "create", create)

    asyncio.run(llm_client.generate_text("first"))
    asyncio.run(llm_client.generate_text("second"))
    assert calls == [
        llm_client.ANTHROPIC_MODEL,
        llm_client.ANTHROPIC_FALLBACK_MODEL,
        llm_client.ANTHROPIC_FALLBACK_MODEL,
    ]

    time.sleep(0.02)
    asyncio.run(llm_client.generate_text("probe"))
    assert calls[-1] == llm_client.ANTHROPIC_MODEL
    assert llm_client.get_llm_health()["routes"]["primary"]["circuit"] == "closed"


def test_authentication_failure_is_not_retried(monkeypatch):
    calls = 0
    request = httpx2.Request("POST", "https://api.test")
    response = httpx2.Response(401, request=request)

    async def create(**_kwargs):
        nonlocal calls
        calls += 1
        raise anthropic.AuthenticationError(
            "invalid key",
            response=response,
            body={"error": {"type": "authentication_error"}},
        )

    monkeypatch.setattr(llm_client._client.messages, "create", create)
    with pytest.raises(anthropic.AuthenticationError):
        asyncio.run(llm_client.generate_text("hello"))
    assert calls == 1
    health = llm_client.get_llm_health()
    assert health["status"] == "unavailable"
    assert health["account"] == {"status": "blocked", "code": "AUTH"}


def test_exhausted_credit_balance_is_treated_as_fatal(monkeypatch):
    calls = 0
    request = httpx2.Request("POST", "https://api.test")
    response = httpx2.Response(400, request=request)

    async def create(**_kwargs):
        nonlocal calls
        calls += 1
        raise anthropic.BadRequestError(
            "credit balance is too low",
            response=response,
            body={
                "error": {
                    "type": "invalid_request_error",
                    "message": "Your credit balance is too low to access the API.",
                }
            },
        )

    monkeypatch.setattr(llm_client._client.messages, "create", create)
    with pytest.raises(anthropic.BadRequestError):
        asyncio.run(llm_client.generate_text("hello"))

    assert calls == 1
    assert llm_client.get_llm_health()["account"] == {
        "status": "blocked",
        "code": "QUOTA",
    }


def test_hanging_provider_obeys_deadline_and_call_budget(monkeypatch):
    calls = 0
    monkeypatch.setattr(llm_client, "LLM_TIMEOUT_SECONDS", 0.01)

    async def create(**_kwargs):
        nonlocal calls
        calls += 1
        await asyncio.sleep(1)
        return _response()

    monkeypatch.setattr(llm_client._client.messages, "create", create)
    started = time.perf_counter()
    with pytest.raises(llm_client.LLMUnavailableError):
        asyncio.run(llm_client.generate_text("hello"))

    assert calls == 3
    assert time.perf_counter() - started < 0.3


def test_health_snapshot_contains_no_prompt_or_credentials(monkeypatch):
    async def create(**_kwargs):
        return _response()

    monkeypatch.setattr(llm_client._client.messages, "create", create)
    asyncio.run(llm_client.generate_text("private profile and secret prompt"))
    snapshot = llm_client.get_llm_health()
    rendered = str(snapshot).lower()

    assert snapshot["status"] == "ok"
    assert set(snapshot["routes"]) == {"primary", "fallback", "fallback_2"}
    assert "private profile" not in rendered
    assert llm_client.ANTHROPIC_MODEL.lower() not in rendered
    assert llm_client.ANTHROPIC_FALLBACK_MODEL.lower() not in rendered
    assert llm_client.ANTHROPIC_FALLBACK_MODEL_2.lower() not in rendered
    assert llm_client.ANTHROPIC_API_KEY.lower() not in rendered


def test_capacity_timeout_does_not_fail_over_or_open_circuits(monkeypatch):
    calls = 0
    monkeypatch.setattr(llm_client, "_call_limiter", asyncio.Semaphore(0))
    monkeypatch.setattr(llm_client, "LLM_QUEUE_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(llm_client, "LLM_CIRCUIT_FAILURE_THRESHOLD", 1)

    async def create(**_kwargs):
        nonlocal calls
        calls += 1
        return _response()

    monkeypatch.setattr(llm_client._client.messages, "create", create)
    with pytest.raises(llm_client.LLMCapacityError):
        asyncio.run(llm_client.generate_text("hello"))

    health = llm_client.get_llm_health()
    assert calls == 0
    assert health["queue_timeouts"] == 1
    assert health["routes"]["primary"]["circuit"] == "closed"
    assert health["routes"]["fallback"]["circuit"] == "closed"


def test_cancelled_half_open_probe_can_recover(monkeypatch):
    monkeypatch.setattr(llm_client, "LLM_CIRCUIT_FAILURE_THRESHOLD", 1)
    monkeypatch.setattr(llm_client, "LLM_CIRCUIT_COOLDOWN_SECONDS", 0.01)
    probe_started = asyncio.Event()
    primary_calls = 0

    async def create(**kwargs):
        nonlocal primary_calls
        if kwargs["model"] == llm_client.ANTHROPIC_FALLBACK_MODEL:
            return _response()
        primary_calls += 1
        if primary_calls == 1:
            raise _timeout()
        if primary_calls == 2:
            probe_started.set()
            await asyncio.sleep(1)
        return _response()

    async def scenario():
        await llm_client.generate_text("open")
        await asyncio.sleep(0.02)
        probe = asyncio.create_task(llm_client.generate_text("cancel probe"))
        await probe_started.wait()
        probe.cancel()
        with suppress(asyncio.CancelledError):
            await probe
        await asyncio.sleep(0.02)
        return await llm_client.generate_text("recover")

    monkeypatch.setattr(llm_client._client.messages, "create", create)
    assert asyncio.run(scenario()) == "ok"
    assert primary_calls == 3
    assert llm_client.get_llm_health()["routes"]["primary"]["circuit"] == "closed"


def test_missing_primary_model_uses_configured_fallback(monkeypatch):
    calls: list[str] = []
    request = httpx2.Request("POST", "https://api.test")
    response = httpx2.Response(404, request=request)

    async def create(**kwargs):
        calls.append(kwargs["model"])
        if kwargs["model"] == llm_client.ANTHROPIC_MODEL:
            raise anthropic.NotFoundError(
                "model unavailable",
                response=response,
                body={"error": {"type": "not_found_error"}},
            )
        return _response("fallback worked")

    monkeypatch.setattr(llm_client._client.messages, "create", create)
    assert asyncio.run(llm_client.generate_text("hello")) == "fallback worked"
    assert calls == [
        llm_client.ANTHROPIC_MODEL,
        llm_client.ANTHROPIC_FALLBACK_MODEL,
    ]


def test_json_parsing_tolerates_a_research_preamble():
    raw = 'Here is what I found:\n\n{"recommendations": [{"name": "Cafe"}]}'
    assert llm_client.parse_json_text(raw) == {
        "recommendations": [{"name": "Cafe"}]
    }
