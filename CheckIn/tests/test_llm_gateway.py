"""Gateway topology: kill switch, passthrough parity, failover, and PII posture."""

from __future__ import annotations

import asyncio
import logging
from contextlib import contextmanager
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

import llm_client
from config import LLM_LOG_PROMPTS


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


@contextmanager
def _gateway(enabled: bool = True, base_url: str = "http://gateway.test/anthropic"):
    """Flip the gateway globals and rebuild the SDK client for one block.

    The globals must be restored before the final rebuild, otherwise the
    module-level client would be rebuilt against the still-patched topology.
    """
    mp = pytest.MonkeyPatch()
    try:
        mp.setattr(llm_client, "LLM_GATEWAY_ENABLED", enabled)
        mp.setattr(llm_client, "LLM_GATEWAY_API_KEY", "sk-virtual-test")
        mp.setattr(llm_client, "LLM_GATEWAY_BASE_URL", base_url)
        llm_client._rebuild_client()
        yield
    finally:
        mp.undo()
        llm_client._rebuild_client()


@pytest.fixture(autouse=True)
def direct_topology():
    """Start every test on the direct path regardless of the shell or .env."""
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


def test_kill_switch_builds_direct_client():
    llm_client._rebuild_client()
    assert "api.anthropic.com" in str(llm_client._client.base_url)
    assert llm_client.get_llm_health()["gateway"] == {
        "enabled": False,
        "mode": "direct",
    }


def test_gateway_builds_passthrough_client():
    with _gateway(base_url="http://gateway.test/anthropic"):
        assert "gateway.test" in str(llm_client._client.base_url)
        assert llm_client.get_llm_health()["gateway"] == {
            "enabled": True,
            "mode": "anthropic_passthrough",
        }
    # Leaving the block is the kill switch: back to the direct topology.
    assert "api.anthropic.com" in str(llm_client._client.base_url)


def test_gateway_and_direct_paths_produce_identical_validated_output(monkeypatch):
    body = '{"recommendations": [{"name": "Cafe", "rating": 4.5}]}'

    async def create(**_kwargs):
        return _response(body)

    with _gateway():
        monkeypatch.setattr(llm_client._client.messages, "create", create)
        gateway_text = asyncio.run(llm_client.generate_text("hello", json_mode=True))

    monkeypatch.setattr(llm_client._client.messages, "create", create)
    direct_text = asyncio.run(llm_client.generate_text("hello", json_mode=True))

    assert llm_client.parse_json_text(gateway_text) == llm_client.parse_json_text(
        direct_text
    )


def test_failover_chain_opus_sonnet_haiku(monkeypatch):
    calls: list[str] = []

    async def create(**kwargs):
        calls.append(kwargs["model"])
        if kwargs["model"] == llm_client.ANTHROPIC_FALLBACK_MODEL_2:
            return _response("last resort answered")
        raise _timeout()

    monkeypatch.setattr(llm_client._client.messages, "create", create)
    text, meta = asyncio.run(llm_client.generate_text("hello", return_meta=True))

    assert text == "last resort answered"
    assert meta == {"model": llm_client.ANTHROPIC_FALLBACK_MODEL_2, "failover": True}
    assert calls == [
        llm_client.ANTHROPIC_MODEL,
        llm_client.ANTHROPIC_FALLBACK_MODEL,
        llm_client.ANTHROPIC_FALLBACK_MODEL_2,
    ]


def test_gateway_path_skips_local_bulkheads(monkeypatch):
    calls = 0

    async def create(**_kwargs):
        nonlocal calls
        calls += 1
        return _response("served")

    monkeypatch.setattr(llm_client, "LLM_QUEUE_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(llm_client, "_call_limiter", asyncio.Semaphore(0))

    with _gateway():
        monkeypatch.setattr(llm_client._client.messages, "create", create)
        assert asyncio.run(llm_client.generate_text("hello")) == "served"
    assert calls == 1

    # The kill switch restores local admission control: the exhausted
    # bulkhead now times out before any provider call is attempted.
    monkeypatch.setattr(llm_client._client.messages, "create", create)
    with pytest.raises(llm_client.LLMCapacityError):
        asyncio.run(llm_client.generate_text("hello"))
    assert calls == 1


def test_refusal_fails_over_on_gateway_path_and_never_leaks(monkeypatch, caplog):
    calls: list[str] = []

    async def create(**kwargs):
        calls.append(kwargs["model"])
        if kwargs["model"] == llm_client.ANTHROPIC_MODEL:
            return _response(
                "",
                stop_reason="refusal",
                stop_details=SimpleNamespace(
                    type="refusal",
                    category="privacy",
                    explanation="private explanation text",
                ),
            )
        return _response("fallback answered")

    with _gateway():
        monkeypatch.setattr(llm_client._client.messages, "create", create)
        with caplog.at_level(logging.INFO):
            assert asyncio.run(llm_client.generate_text("hello")) == "fallback answered"
        snapshot = llm_client.get_llm_health()

    assert calls == [
        llm_client.ANTHROPIC_MODEL,
        llm_client.ANTHROPIC_FALLBACK_MODEL,
    ]
    assert snapshot["routes"]["primary"]["refusals"] == 1
    assert "private explanation" not in str(snapshot)
    assert "private explanation" not in caplog.text


def test_app_logs_only_prompt_hash_regardless_of_log_prompts_flag(monkeypatch, caplog):
    async def create(**_kwargs):
        return _response()

    monkeypatch.setattr(llm_client._client.messages, "create", create)
    with caplog.at_level(logging.INFO):
        asyncio.run(llm_client.generate_text("super secret personal profile text"))

    assert "super secret" not in caplog.text
    assert "prompt_sha=" in caplog.text
    # LLM_LOG_PROMPTS governs gateway-side capture only; the app itself is
    # hash-only unconditionally, and the flag ships off.
    assert LLM_LOG_PROMPTS is False


@pytest.mark.parametrize("gateway_enabled", [False, True])
def test_gateway_kill_switch_paths_pass_same_core_assertions(
    monkeypatch, gateway_enabled
):
    calls: list[str] = []

    async def create(**kwargs):
        calls.append(kwargs["model"])
        if kwargs["model"] == llm_client.ANTHROPIC_MODEL:
            raise _timeout()
        return _response("recovered")

    with _gateway(enabled=gateway_enabled):
        monkeypatch.setattr(llm_client._client.messages, "create", create)
        assert asyncio.run(llm_client.generate_text("hello")) == "recovered"
        health = llm_client.get_llm_health()

    assert calls == [
        llm_client.ANTHROPIC_MODEL,
        llm_client.ANTHROPIC_FALLBACK_MODEL,
    ]
    assert health["status"] == "ok"
    assert health["gateway"]["enabled"] is gateway_enabled
