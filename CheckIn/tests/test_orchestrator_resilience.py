"""Agent isolation, deadlines, and deterministic context fallback."""

from __future__ import annotations

import asyncio
from datetime import date

import orchestrator
from llm_client import LLMError
from schemas import AgentResult, GroupType, Recommendation, TripPreferences


def _prefs() -> TripPreferences:
    return TripPreferences(
        destination="Kyoto",
        origin="Mumbai",
        start_date=date(2026, 10, 12),
        end_date=date(2026, 10, 18),
        budget_amount=3200,
        currency="USD",
        vibes=["culture", "food"],
        group_type=GroupType.COUPLE,
        num_travelers=2,
    )


def _result(agent_name: str, category: str = "activity") -> AgentResult:
    recommendations = [
        Recommendation(
            id=f"{agent_name}-{rank}",
            name=f"Option {rank}",
            category=category,
            description="A verified option with a strong sense of place.",
            reasoning="It matches the travel profile.",
            estimated_cost="$40-$80",
            cost_min=40,
            cost_max=80,
            rating=4.7,
            review_count=900,
            location="Central Kyoto",
            image_search_query="kyoto travel",
        )
        for rank in range(1, 4)
    ]
    return AgentResult(agent_name=agent_name, recommendations=recommendations)


async def _events(generator) -> list[dict]:
    return [event async for event in generator]


def test_context_failure_uses_deterministic_brief(monkeypatch):
    async def fail(*_args, **_kwargs):
        raise RuntimeError("provider-secret")

    monkeypatch.setattr(orchestrator, "generate_text", fail)
    monkeypatch.setattr(orchestrator, "LLM_ENHANCE_CONTEXT_BRIEF", True)
    brief = asyncio.run(
        orchestrator.generate_context_brief(
            _prefs(),
            "Prefers quiet local restaurants and an unhurried pace.",
        )
    )

    assert "Mumbai to Kyoto" in brief
    assert "3200 USD" in brief
    assert "quiet local restaurants" in brief
    assert "provider-secret" not in brief


def test_one_agent_timeout_does_not_stop_successful_sibling(monkeypatch):
    class FastAgent:
        agent_name = "Fast Agent"

        async def run(self, *_args, **_kwargs):
            return _result(self.agent_name)

    class SlowAgent:
        agent_name = "Slow Agent"

        async def run(self, *_args, **_kwargs):
            await asyncio.sleep(1)
            return _result(self.agent_name)

    monkeypatch.setattr(orchestrator, "AGENT_DEADLINE_SECONDS", 0.01)
    monkeypatch.setattr(orchestrator, "SSE_HEARTBEAT_SECONDS", 0.005)
    monkeypatch.setattr(
        orchestrator,
        "_get_agents",
        lambda _names=None: [FastAgent(), SlowAgent()],
    )

    events = asyncio.run(
        _events(orchestrator.run_agents_streaming(_prefs(), "context"))
    )

    assert any(event["event"] == "agent_completed" and event["agent"] == "Fast Agent" for event in events)
    failure = next(event for event in events if event["event"] == "agent_failed")
    assert failure["agent"] == "Slow Agent"
    assert failure["code"] == "AGENT_TIMEOUT"
    assert events[-1] == {
        "event": "all_complete",
        "completed": 1,
        "failed": 1,
        "status": "partial",
    }


def test_agent_subset_starts_only_requested_category(monkeypatch):
    async def succeed(agent, *_args, **_kwargs):
        return _result(agent.agent_name, "transport")

    monkeypatch.setattr(orchestrator, "_run_agent", succeed)
    events = asyncio.run(
        _events(
            orchestrator.run_agents_streaming(
                _prefs(),
                "context",
                agent_names=["Transport Agent"],
            )
        )
    )

    started = [event["agent"] for event in events if event["event"] == "agent_started"]
    assert started == ["Transport Agent"]
    assert events[-1]["completed"] == 1
    assert events[-1]["failed"] == 0


def test_provider_details_never_enter_failure_event(monkeypatch):
    class BrokenAgent:
        agent_name = "Broken Agent"

        async def run(self, *_args, **_kwargs):
            raise RuntimeError("secret-provider-payload")

    monkeypatch.setattr(
        orchestrator,
        "_get_agents",
        lambda _names=None: [BrokenAgent()],
    )
    events = asyncio.run(
        _events(orchestrator.run_agents_streaming(_prefs(), "context"))
    )

    rendered = str(events)
    assert "secret-provider-payload" not in rendered
    assert "retry safely" in rendered


def test_fatal_agent_failure_is_not_marked_retryable(monkeypatch):
    class MisconfiguredAgent:
        agent_name = "Misconfigured Agent"

        async def run(self, *_args, **_kwargs):
            raise LLMError("bad credentials", code="AUTH", retryable=False)

    monkeypatch.setattr(
        orchestrator,
        "_get_agents",
        lambda _names=None: [MisconfiguredAgent()],
    )
    events = asyncio.run(
        _events(orchestrator.run_agents_streaming(_prefs(), "context"))
    )

    failure = next(event for event in events if event["event"] == "agent_failed")
    assert failure["retryable"] is False
    assert failure["code"] == "AGENT_CONFIGURATION_ERROR"
    assert "credentials" not in str(failure)
