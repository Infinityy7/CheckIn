"""Itinerary generator — assembles user selections into a day-by-day plan."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import unicodedata
from datetime import date, timedelta

from config import ITINERARY_DEADLINE_SECONDS, MAX_AGENT_RETRIES
from llm_client import LLMCapacityError, generate_text, is_fatal_error, parse_json_text
from prompts.itinerary import build_itinerary_prompt
from schemas import Itinerary, Recommendation, TripPreferences, planning_scope_note

logger = logging.getLogger(__name__)

ITINERARY_SYSTEM = (
    "You are an expert travel itinerary architect. You create beautifully structured, "
    "practical day-by-day travel plans that balance must-see highlights with breathing room. "
    "You think geographically to minimize wasted transit time, and you always include "
    "helpful local tips. Respond with valid JSON only."
)

ITEM_CATEGORIES = frozenset({"accommodation", "activity", "restaurant", "transport", "free_time"})


class ItineraryValidationError(ValueError):
    """The model returned well-formed JSON that does not describe this trip."""


class ItineraryOmittedSelections(ItineraryValidationError):
    """The plan left out recommendations the traveler explicitly selected."""

    def __init__(self, names: list[str]) -> None:
        super().__init__(f"Itinerary omitted selected recommendations: {', '.join(names)}")
        self.names = names


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    ascii_only = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", ascii_only.lower()).split())


def missing_recommendations(itinerary: Itinerary, selected: list[Recommendation]) -> list[str]:
    """Selected names that never appear in any item title or description."""
    haystack = _normalize(
        " ".join(f"{item.title} {item.description}" for day in itinerary.days for item in day.items)
    )
    return [rec.name for rec in selected if _normalize(rec.name) not in haystack]


def validate_itinerary(
    itinerary: Itinerary,
    prefs: TripPreferences,
    selected: list[Recommendation],
) -> None:
    """Reject plans that do not match the trip; normalizes item categories in place."""
    if not itinerary.days:
        raise ItineraryValidationError("Itinerary has no days")
    span = (prefs.end_date - prefs.start_date).days + 1
    expected = [prefs.start_date + timedelta(days=offset) for offset in range(span)]
    if len(itinerary.days) != span:
        raise ItineraryValidationError(
            f"Itinerary has {len(itinerary.days)} days but the trip runs "
            f"{prefs.start_date.isoformat()} to {prefs.end_date.isoformat()} ({span} days)"
        )
    for position, (day, wanted) in enumerate(zip(itinerary.days, expected), start=1):
        if day.day_number != position:
            raise ItineraryValidationError(
                f"day_number must run 1..{span} in order; position {position} has day_number {day.day_number}"
            )
        try:
            actual = date.fromisoformat(day.date.strip())
        except ValueError as exc:
            raise ItineraryValidationError(f"Day {position} has a non-ISO date {day.date!r}") from exc
        if actual != wanted:
            raise ItineraryValidationError(
                f"Day {position} is dated {actual.isoformat()} but must be {wanted.isoformat()}"
            )
        if not day.items:
            raise ItineraryValidationError(f"Day {position} has no items")
        for item in day.items:
            category = item.category.strip().lower()
            if category not in ITEM_CATEGORIES:
                raise ItineraryValidationError(
                    f"Day {position} item {item.title!r} uses unknown category {item.category!r}; "
                    f"allowed: {', '.join(sorted(ITEM_CATEGORIES))}"
                )
            item.category = category
    missing = missing_recommendations(itinerary, selected)
    if missing:
        raise ItineraryOmittedSelections(missing)


async def generate_itinerary(
    prefs: TripPreferences,
    context_brief: str,
    selected_recommendations: list[Recommendation],
    exact_choices: list[dict] | None = None,
) -> Itinerary:
    """Build an itinerary behind a strict wall-clock deadline."""
    try:
        async with asyncio.timeout(ITINERARY_DEADLINE_SECONDS):
            return await _generate_itinerary(
                prefs,
                context_brief,
                selected_recommendations,
                exact_choices or [],
            )
    except TimeoutError as exc:
        raise RuntimeError("Itinerary generation reached its safe time limit") from exc


async def _generate_itinerary(
    prefs: TripPreferences,
    context_brief: str,
    selected_recommendations: list[Recommendation],
    exact_choices: list[dict],
) -> Itinerary:
    """Ask the LLM to build the itinerary from the user's picks.

    Retries on bad JSON, asking for shorter output since the usual
    problem is the response getting cut off. A plan that parses but fails
    semantic validation also counts as a failed attempt and gets a targeted
    correction on the next try.
    """
    recs_json = json.dumps(
        [rec.model_dump() for rec in selected_recommendations], indent=2
    )
    user_prompt = build_itinerary_prompt(
        prefs.model_dump_json(indent=2),
        context_brief,
        recs_json,
        exact_choices_json=json.dumps(exact_choices, indent=2) if exact_choices else None,
        scope_note=planning_scope_note(prefs.scope),
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
                max_tokens=16384,
                prefer_fallback=attempt > 1,
                json_mode=True,
                effort="high",
                operation="itinerary",
            )
            data = parse_json_text(raw_text)
            itinerary = Itinerary(**data)
            validate_itinerary(itinerary, prefs, selected_recommendations)
            logger.info(
                "Itinerary generated: '%s' with %d days",
                itinerary.trip_title, len(itinerary.days),
            )
            return itinerary
        except ItineraryValidationError as exc:
            last_error = exc
            logger.warning("Itinerary attempt %d rejected: %s", attempt, exc)
            prompt = (
                user_prompt
                + "\n\nIMPORTANT: Your previous plan was rejected because: "
                + str(exc)
                + ". Fix exactly that. Keep every other rule. Respond with ONLY valid JSON, no markdown fences."
            )
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

    if isinstance(last_error, ItineraryOmittedSelections):
        logger.error("Itinerary omitted selected recommendations after %d attempts: %s", max_attempts, last_error.names)
        raise RuntimeError(str(last_error)) from last_error
    raise RuntimeError(
        f"Itinerary generation failed after {max_attempts} bounded attempts"
    ) from last_error
