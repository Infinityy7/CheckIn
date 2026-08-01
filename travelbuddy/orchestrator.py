"""Orchestrate specialist travel agents with bounded, partial-safe execution."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import AsyncGenerator, Iterable

from agents import AccommodationAgent, ActivitiesAgent, RestaurantAgent, TransportAgent
from agents.base import BaseAgent
from config import (
    AGENT_DEADLINE_SECONDS,
    CONTEXT_BRIEF_TIMEOUT_SECONDS,
    LLM_ENHANCE_CONTEXT_BRIEF,
    SSE_HEARTBEAT_SECONDS,
)
from llm_client import generate_text, is_fatal_error
from prompts.context_brief import build_context_brief_prompt
from schemas import AgentResult, TripPreferences

logger = logging.getLogger(__name__)

_AGENT_TYPES = (
    AccommodationAgent,
    ActivitiesAgent,
    RestaurantAgent,
    TransportAgent,
)
ALL_AGENT_NAMES = tuple(agent_type().agent_name for agent_type in _AGENT_TYPES)


@dataclass(frozen=True)
class AgentFailure:
    """Internal marker that keeps provider details out of the public stream."""

    agent_name: str
    message: str
    code: str = "AGENT_FAILED"
    retryable: bool = True


def _compact(value: str, limit: int = 700) -> str:
    return re.sub(r"\s+", " ", value).strip()[:limit]


def build_fallback_context_brief(
    prefs: TripPreferences,
    profile_sketch: str | None = None,
    cotraveller_sketches: list[str] | None = None,
) -> str:
    """Build a useful context brief without depending on any model."""
    days = (prefs.end_date - prefs.start_date).days + 1
    group = getattr(prefs.group_type, "value", prefs.group_type)
    vibes = ", ".join(prefs.vibes)
    brief = (
        f"Plan a {days}-day trip from {prefs.origin} to {prefs.destination} "
        f"from {prefs.start_date.isoformat()} to {prefs.end_date.isoformat()} for "
        f"{prefs.num_travelers} traveler(s) traveling as a {group}. The total "
        f"budget is {prefs.budget_amount:g} {prefs.currency}. Prioritize these "
        f"requested travel styles: {vibes}."
    )
    if profile_sketch:
        brief += f" Saved traveler preferences: {_compact(profile_sketch)}."
    if cotraveller_sketches:
        notes = " | ".join(_compact(sketch, 300) for sketch in cotraveller_sketches)
        brief += f" Co-traveler preferences: {notes}."
    return brief


async def generate_context_brief(
    prefs: TripPreferences,
    profile_sketch: str | None = None,
    cotraveller_sketches: list[str] | None = None,
) -> str:
    """Produce a shared brief, falling back deterministically on any AI failure."""
    fallback = build_fallback_context_brief(
        prefs,
        profile_sketch,
        cotraveller_sketches,
    )
    if not LLM_ENHANCE_CONTEXT_BRIEF:
        return fallback
    taste_notes = None
    if profile_sketch:
        parts = ["## Traveler taste profile\n" + profile_sketch]
        if cotraveller_sketches:
            for sketch in cotraveller_sketches:
                parts.append("## Co-traveller\n" + sketch)
        taste_notes = "\n\n".join(parts)

    prompt = build_context_brief_prompt(prefs.model_dump_json(indent=2), taste_notes)

    try:
        async with asyncio.timeout(CONTEXT_BRIEF_TIMEOUT_SECONDS):
            brief = await generate_text(
                prompt,
                max_output_tokens=512,
                temperature=0.5,
                operation="context_brief",
            )
        brief = brief.strip()
        if not brief:
            raise ValueError("empty context brief")
        return brief
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning(
            "Context brief enhancement failed; using deterministic brief error=%s",
            type(exc).__name__,
        )
        return fallback


async def _run_agent(
    agent: BaseAgent,
    prefs: TripPreferences,
    context_brief: str,
    user_taste: dict | None = None,
    cotraveller_tastes: list[dict] | None = None,
) -> AgentResult | AgentFailure:
    """Run one agent behind a hard wall-clock deadline."""
    try:
        async with asyncio.timeout(AGENT_DEADLINE_SECONDS):
            return await agent.run(
                prefs,
                context_brief,
                user_taste,
                cotraveller_tastes,
            )
    except TimeoutError:
        logger.error("Agent timed out agent=%s", agent.agent_name)
        return AgentFailure(
            agent_name=agent.agent_name,
            message=(
                f"{agent.agent_name} took too long. Your other results are safe, "
                "and this category can be retried."
            ),
            code="AGENT_TIMEOUT",
        )
    except Exception as exc:
        retryable = not is_fatal_error(exc)
        logger.error(
            "Agent failed agent=%s error=%s",
            agent.agent_name,
            type(exc).__name__,
            exc_info=True,
        )
        return AgentFailure(
            agent_name=agent.agent_name,
            message=(
                f"{agent.agent_name} could not finish this search. "
                + ("You can retry safely." if retryable else "The AI service needs attention.")
            ),
            code="AGENT_FAILED" if retryable else "AGENT_CONFIGURATION_ERROR",
            retryable=retryable,
        )


def _get_agents(agent_names: Iterable[str] | None = None) -> list[BaseAgent]:
    """Return all agents or a validated subset, preserving canonical order."""
    agents = [agent_type() for agent_type in _AGENT_TYPES]
    if agent_names is None:
        return agents
    requested = set(agent_names)
    unknown = requested.difference(ALL_AGENT_NAMES)
    if unknown:
        raise ValueError(f"Unknown agent names: {sorted(unknown)}")
    return [agent for agent in agents if agent.agent_name in requested]


async def run_agents_streaming(
    prefs: TripPreferences,
    context_brief: str,
    user_taste: dict | None = None,
    cotraveller_tastes: list[dict] | None = None,
    agent_names: Iterable[str] | None = None,
) -> AsyncGenerator[dict, None]:
    """Run agents in parallel and keep every category isolated from failures."""
    agents = _get_agents(agent_names)

    async def _wrapped(agent: BaseAgent) -> tuple[BaseAgent, AgentResult | AgentFailure]:
        result = await _run_agent(
            agent,
            prefs,
            context_brief,
            user_taste,
            cotraveller_tastes,
        )
        return agent, result

    for agent in agents:
        yield {"event": "agent_started", "agent": agent.agent_name}

    pending = {asyncio.create_task(_wrapped(agent)) for agent in agents}
    completed = 0
    failed = 0
    try:
        while pending:
            done, pending = await asyncio.wait(
                pending,
                timeout=SSE_HEARTBEAT_SECONDS,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                yield {"event": "heartbeat"}
                continue

            for task in done:
                agent, result = await task
                if isinstance(result, AgentResult):
                    completed += 1
                    yield {
                        "event": "agent_completed",
                        "agent": agent.agent_name,
                        "results": [r.model_dump() for r in result.recommendations],
                    }
                else:
                    failed += 1
                    yield {
                        "event": "agent_failed",
                        "agent": agent.agent_name,
                        "error": result.message,
                        "code": result.code,
                        "retryable": result.retryable,
                    }
    finally:
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    if failed == 0:
        status = "complete"
    elif completed:
        status = "partial"
    else:
        status = "failed"
    yield {
        "event": "all_complete",
        "completed": completed,
        "failed": failed,
        "status": status,
    }
