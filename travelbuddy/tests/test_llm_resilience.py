"""Bounded failover, circuit breaking, and output-mode behavior."""

from __future__ import annotations

import asyncio
import time
from contextlib import suppress
from types import SimpleNamespace

import httpx
import openai
import pytest

import llm_client


def _response(text: str = "ok") -> SimpleNamespace:
    return SimpleNamespace(
        output_text=text,
        usage=SimpleNamespace(input_tokens=10, output_tokens=4),
    )


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
        if kwargs["model"] == llm_client.OPENAI_MODEL:
            raise openai.APITimeoutError(request=httpx.Request("POST", "https://api.test"))
        return _response('{"recommendations": []}')

    monkeypatch.setattr(llm_client._client.responses, "create", create)
    result = asyncio.run(
        llm_client.generate_text("hello", json_mode=True, use_search=True)
    )

    assert result == '{"recommendations": []}'
    assert [call["model"] for call in calls] == [
        llm_client.OPENAI_MODEL,
        llm_client.OPENAI_FALLBACK_MODEL,
    ]
    assert calls[1]["text"] == {"format": {"type": "json_object"}}
    assert calls[1]["max_tool_calls"] == llm_client.LLM_MAX_WEB_SEARCH_CALLS


def test_open_circuit_skips_primary_then_allows_one_probe(monkeypatch):
    calls: list[str] = []
    primary_failures = [True]
    monkeypatch.setattr(llm_client, "LLM_CIRCUIT_FAILURE_THRESHOLD", 1)
    monkeypatch.setattr(llm_client, "LLM_CIRCUIT_COOLDOWN_SECONDS", 0.01)

    async def create(**kwargs):
        model = kwargs["model"]
        calls.append(model)
        if model == llm_client.OPENAI_MODEL and primary_failures:
            primary_failures.pop()
            raise openai.APITimeoutError(request=httpx.Request("POST", "https://api.test"))
        return _response()

    monkeypatch.setattr(llm_client._client.responses, "create", create)

    asyncio.run(llm_client.generate_text("first"))
    asyncio.run(llm_client.generate_text("second"))
    assert calls == [
        llm_client.OPENAI_MODEL,
        llm_client.OPENAI_FALLBACK_MODEL,
        llm_client.OPENAI_FALLBACK_MODEL,
    ]

    time.sleep(0.02)
    asyncio.run(llm_client.generate_text("probe"))
    assert calls[-1] == llm_client.OPENAI_MODEL
    assert llm_client.get_llm_health()["routes"]["primary"]["circuit"] == "closed"


def test_authentication_failure_is_not_retried(monkeypatch):
    calls = 0
    request = httpx.Request("POST", "https://api.test")
    response = httpx.Response(401, request=request)

    async def create(**_kwargs):
        nonlocal calls
        calls += 1
        raise openai.AuthenticationError(
            "invalid key",
            response=response,
            body={"error": {"code": "invalid_api_key"}},
        )

    monkeypatch.setattr(llm_client._client.responses, "create", create)
    with pytest.raises(openai.AuthenticationError):
        asyncio.run(llm_client.generate_text("hello"))
    assert calls == 1
    health = llm_client.get_llm_health()
    assert health["status"] == "unavailable"
    assert health["account"] == {"status": "blocked", "code": "AUTH"}


def test_hanging_provider_obeys_deadline_and_call_budget(monkeypatch):
    calls = 0
    monkeypatch.setattr(llm_client, "LLM_TIMEOUT_SECONDS", 0.01)

    async def create(**_kwargs):
        nonlocal calls
        calls += 1
        await asyncio.sleep(1)
        return _response()

    monkeypatch.setattr(llm_client._client.responses, "create", create)
    started = time.perf_counter()
    with pytest.raises(llm_client.LLMUnavailableError):
        asyncio.run(llm_client.generate_text("hello"))

    assert calls == 2
    assert time.perf_counter() - started < 0.2


def test_health_snapshot_contains_no_prompt_or_credentials(monkeypatch):
    async def create(**_kwargs):
        return _response()

    monkeypatch.setattr(llm_client._client.responses, "create", create)
    asyncio.run(llm_client.generate_text("private profile and secret prompt"))
    snapshot = llm_client.get_llm_health()
    rendered = str(snapshot).lower()

    assert snapshot["status"] == "ok"
    assert set(snapshot["routes"]) == {"primary", "fallback"}
    assert "private profile" not in rendered
    assert llm_client.OPENAI_MODEL.lower() not in rendered
    assert llm_client.OPENAI_FALLBACK_MODEL.lower() not in rendered
    assert llm_client.OPENAI_API_KEY.lower() not in rendered


def test_capacity_timeout_does_not_fail_over_or_open_circuits(monkeypatch):
    calls = 0
    monkeypatch.setattr(llm_client, "_call_limiter", asyncio.Semaphore(0))
    monkeypatch.setattr(llm_client, "LLM_QUEUE_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(llm_client, "LLM_CIRCUIT_FAILURE_THRESHOLD", 1)

    async def create(**_kwargs):
        nonlocal calls
        calls += 1
        return _response()

    monkeypatch.setattr(llm_client._client.responses, "create", create)
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
        if kwargs["model"] == llm_client.OPENAI_FALLBACK_MODEL:
            return _response()
        primary_calls += 1
        if primary_calls == 1:
            raise openai.APITimeoutError(request=httpx.Request("POST", "https://api.test"))
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

    monkeypatch.setattr(llm_client._client.responses, "create", create)
    assert asyncio.run(scenario()) == "ok"
    assert primary_calls == 3
    assert llm_client.get_llm_health()["routes"]["primary"]["circuit"] == "closed"


def test_missing_primary_model_uses_configured_fallback(monkeypatch):
    calls: list[str] = []
    request = httpx.Request("POST", "https://api.test")
    response = httpx.Response(404, request=request)

    async def create(**kwargs):
        calls.append(kwargs["model"])
        if kwargs["model"] == llm_client.OPENAI_MODEL:
            raise openai.NotFoundError(
                "model unavailable",
                response=response,
                body={"error": {"code": "model_not_found"}},
            )
        return _response("fallback worked")

    monkeypatch.setattr(llm_client._client.responses, "create", create)
    assert asyncio.run(llm_client.generate_text("hello")) == "fallback worked"
    assert calls == [llm_client.OPENAI_MODEL, llm_client.OPENAI_FALLBACK_MODEL]
