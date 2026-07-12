"""Orchestrator that runs all specialist agents in parallel."""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncGenerator

from google import genai
from google.genai import types

from agents import AccommodationAgent, ActivitiesAgent, RestaurantAgent, TransportAgent
from agents.base import BaseAgent
from config import GEMINI_API_KEY, GEMINI_MODEL, GEMINI_FALLBACK_MODEL
from prompts.context_brief import build_context_brief_prompt
from schemas import AgentResult, OrchestratorResult, TripPreferences

logger = logging.getLogger(__name__)


async def generate_context_brief(prefs: TripPreferences) -> str:
    """Use Gemini to produce a shared trip context paragraph for all agents."""
    client = genai.Client(api_key=GEMINI_API_KEY)
    prompt = build_context_brief_prompt(prefs.model_dump_json(indent=2))

    logger.info("Generating trip context brief...")
    brief = None
    for model in [GEMINI_MODEL, GEMINI_FALLBACK_MODEL]:
        try:
            response = await client.aio.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    max_output_tokens=512,
                    temperature=0.7,
                ),
            )
            brief = response.text.strip()
            break
        except Exception as exc:
            if "503" in str(exc) or "UNAVAILABLE" in str(exc):
                logger.warning("Context brief: %s overloaded, trying fallback...", model)
                await asyncio.sleep(1)
                continue
            raise

    if not brief:
        raise RuntimeError("All Gemini models unavailable")
    logger.info("Context brief: %s", brief)
    return brief


async def _run_agent(agent: BaseAgent, prefs: TripPreferences, context_brief: str) -> AgentResult | str:
    """Run a single agent, returning the result or an error string on failure."""
    try:
        return await agent.run(prefs, context_brief)
    except Exception as exc:
        logger.error("Agent %s failed: %s", agent.agent_name, exc, exc_info=True)
        return f"{agent.agent_name}: {exc}"


def _get_agents() -> list[BaseAgent]:
    """Return all specialist agent instances."""
    return [
        AccommodationAgent(),
        ActivitiesAgent(),
        RestaurantAgent(),
        TransportAgent(),
    ]


async def run_all_agents(prefs: TripPreferences) -> OrchestratorResult:
    """Generate the context brief, then run all 4 agents in parallel.

    Handles partial failures: if some agents fail, the successful results are
    still returned alongside a list of error messages.
    """
    context_brief = await generate_context_brief(prefs)
    agents = _get_agents()

    logger.info("Launching %d agents in parallel...", len(agents))
    raw_results = await asyncio.gather(
        *[_run_agent(agent, prefs, context_brief) for agent in agents]
    )

    agent_results: list[AgentResult] = []
    errors: list[str] = []

    for result in raw_results:
        if isinstance(result, AgentResult):
            agent_results.append(result)
        else:
            errors.append(result)

    if errors:
        logger.warning("Some agents failed: %s", errors)
    logger.info(
        "Orchestrator complete: %d succeeded, %d failed",
        len(agent_results), len(errors),
    )

    return OrchestratorResult(
        trip_context_brief=context_brief,
        agent_results=agent_results,
        errors=errors,
    )


async def run_agents_streaming(
    prefs: TripPreferences,
    context_brief: str,
) -> AsyncGenerator[dict, None]:
    """Run all agents in parallel and yield SSE-style event dicts as each finishes.

    Events yielded:
      {"event": "agent_started", "agent": "<name>"}
      {"event": "agent_completed", "agent": "<name>", "results": [...]}
      {"event": "agent_failed", "agent": "<name>", "error": "..."}
      {"event": "all_complete"}
    """
    agents = _get_agents()

    async def _wrapped(agent: BaseAgent, delay: float) -> tuple[BaseAgent, AgentResult | str]:
        if delay > 0:
            await asyncio.sleep(delay)
        return agent, await _run_agent(agent, prefs, context_brief)

    # Emit all started events first
    for agent in agents:
        yield {"event": "agent_started", "agent": agent.agent_name}

    # Stagger launches by 1s each to avoid Gemini rate limits
    tasks = [asyncio.create_task(_wrapped(agent, i * 1.0)) for i, agent in enumerate(agents)]

    for coro in asyncio.as_completed(tasks):
        agent, result = await coro
        if isinstance(result, AgentResult):
            yield {
                "event": "agent_completed",
                "agent": agent.agent_name,
                "results": [r.model_dump() for r in result.recommendations],
            }
        else:
            yield {
                "event": "agent_failed",
                "agent": agent.agent_name,
                "error": result,
            }

    yield {"event": "all_complete"}
