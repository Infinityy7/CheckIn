"""Itinerary generator — assembles user selections into a day-by-day plan."""

from __future__ import annotations

import asyncio
import json
import logging

from config import ITINERARY_DEADLINE_SECONDS, MAX_AGENT_RETRIES
from llm_client import LLMCapacityError, generate_text, is_fatal_error, parse_json_text
from prompts.itinerary import build_itinerary_prompt
from schemas import Itinerary, Recommendation, TripPreferences

logger = logging.getLogger(__name__)

ITINERARY_SYSTEM = (
    "You are an expert travel itinerary architect. You create beautifully structured, "
    "practical day-by-day travel plans that balance must-see highlights with breathing room. "
    "You think geographically to minimize wasted transit time, and you always include "
    "helpful local tips. Respond with valid JSON only."
)


async def generate_itinerary(
    prefs: TripPreferences,
    context_brief: str,
    selected_recommendations: list[Recommendation],
) -> Itinerary:
    """Build an itinerary behind a strict wall-clock deadline."""
    try:
        async with asyncio.timeout(ITINERARY_DEADLINE_SECONDS):
            return await _generate_itinerary(
                prefs,
                context_brief,
                selected_recommendations,
            )
    except TimeoutError as exc:
        raise RuntimeError("Itinerary generation reached its safe time limit") from exc


async def _generate_itinerary(
    prefs: TripPreferences,
    context_brief: str,
    selected_recommendations: list[Recommendation],
) -> Itinerary:
    """Ask the LLM to build the itinerary from the user's picks.

    Retries on bad JSON, asking for shorter output since the usual
    problem is the response getting cut off.
    """
    recs_json = json.dumps(
        [rec.model_dump() for rec in selected_recommendations], indent=2
    )
    user_prompt = build_itinerary_prompt(
        prefs.model_dump_json(indent=2), context_brief, recs_json
    )

    max_attempts = MAX_AGENT_RETRIES
    prompt = user_prompt
    last_error: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        logger.info("Itinerary generation attempt %d/%d", attempt, max_attempts)

        try:
            raw_text = await generate_text(
                prompt,
                system_instruction=ITINERARY_SYSTEM,
                max_output_tokens=16384,
                temperature=0.5,
                prefer_fallback=attempt > 1,
                json_mode=True,
                operation="itinerary",
            )
            data = parse_json_text(raw_text)
            itinerary = Itinerary(**data)
            logger.info(
                "Itinerary generated: '%s' with %d days",
                itinerary.trip_title, len(itinerary.days),
            )
            return itinerary
        except Exception as exc:
            if isinstance(exc, LLMCapacityError):
                raise
            if is_fatal_error(exc):
                raise
            last_error = exc
            logger.warning(
                "Itinerary attempt %d failed: %s",
                attempt,
                type(exc).__name__,
            )
            prompt = (
                user_prompt
                + "\n\nIMPORTANT: Your previous response was invalid or incomplete. "
                "Keep descriptions SHORT (1 sentence each). Keep tips to one short sentence or null. "
                "Use 4-5 items per day max. Respond with ONLY valid JSON, no markdown fences."
            )

    raise RuntimeError(
        f"Itinerary generation failed after {max_attempts} bounded attempts"
    ) from last_error
