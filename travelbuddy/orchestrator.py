"""Orchestrator that runs all specialist agents in parallel."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import AsyncGenerator

from agents import AccommodationAgent, ActivitiesAgent, RestaurantAgent, TransportAgent
from agents.base import BaseAgent
from llm_client import generate_text
from prompts.context_brief import build_context_brief_prompt
from schemas import AgentResult, TripPreferences

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentFailure:
    """Internal marker that keeps provider details out of the public event stream."""

    agent_name: str
    message: str


async def generate_context_brief(
    prefs: TripPreferences,
    profile_sketch: str | None = None,
    cotraveller_sketches: list[str] | None = None,
) -> str:
    """Produce a shared trip context paragraph for all agents.

    Taste sketches (if the user has them) get folded in here so all
    4 agents inherit the personalization from this one call.
    """
    taste_notes = None
    if profile_sketch:
        parts = ["## Traveler taste profile\n" + profile_sketch]
        if cotraveller_sketches:
            for sketch in cotraveller_sketches:
                parts.append("## Co-traveller\n" + sketch)
        taste_notes = "\n\n".join(parts)

    prompt = build_context_brief_prompt(prefs.model_dump_json(indent=2), taste_notes)

    logger.info("Generating trip context brief...")
    brief = await generate_text(
        prompt,
        max_output_tokens=512,
        temperature=0.7,  # it's prose, a bit of creativity is fine
    )
    brief = brief.strip()
    logger.info("Context brief: %s", brief)
    return brief


async def _run_agent(
    agent: BaseAgent,
    prefs: TripPreferences,
    context_brief: str,
    user_taste: dict | None = None,
    cotraveller_tastes: list[dict] | None = None,
) -> AgentResult | AgentFailure:
    """Run one agent and return a user-safe failure when it cannot finish."""
    try:
        return await agent.run(prefs, context_brief, user_taste, cotraveller_tastes)
    except Exception as exc:
        logger.error("Agent %s failed: %s", agent.agent_name, exc, exc_info=True)
        return AgentFailure(
            agent_name=agent.agent_name,
            message=f"{agent.agent_name} could not finish this search. You can retry safely.",
        )


def _get_agents() -> list[BaseAgent]:
    """Return all specialist agent instances."""
    return [
        AccommodationAgent(),
        ActivitiesAgent(),
        RestaurantAgent(),
        TransportAgent(),
    ]


async def run_agents_streaming(
    prefs: TripPreferences,
    context_brief: str,
    user_taste: dict | None = None,
    cotraveller_tastes: list[dict] | None = None,
) -> AsyncGenerator[dict, None]:
    """Run all agents in parallel and yield SSE-style event dicts as each finishes.

    llm_client caps how many API calls run at once, so launching
    all agents together is fine.

    Events yielded:
      {"event": "agent_started", "agent": "<name>"}
      {"event": "agent_completed", "agent": "<name>", "results": [...]}
      {"event": "agent_failed", "agent": "<name>", "error": "..."}
      {"event": "all_complete"}
    """
    agents = _get_agents()

    async def _wrapped(agent: BaseAgent) -> tuple[BaseAgent, AgentResult | AgentFailure]:
        return agent, await _run_agent(agent, prefs, context_brief, user_taste, cotraveller_tastes)

    for agent in agents:
        yield {"event": "agent_started", "agent": agent.agent_name}

    tasks = [asyncio.create_task(_wrapped(agent)) for agent in agents]

    completed = 0
    failed = 0
    for coro in asyncio.as_completed(tasks):
        agent, result = await coro
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
                "code": "AGENT_FAILED",
                "retryable": True,
            }

    yield {"event": "all_complete", "completed": completed, "failed": failed}
