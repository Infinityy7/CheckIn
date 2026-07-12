"""Itinerary generator — assembles user selections into a day-by-day plan."""

from __future__ import annotations

import json
import logging

from google import genai
from google.genai import types

from config import GEMINI_API_KEY, GEMINI_MODEL, GEMINI_FALLBACK_MODEL, MAX_AGENT_RETRIES
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
    """Call Gemini to produce a structured itinerary from the user's selections.

    Retries up to MAX_AGENT_RETRIES times if JSON parsing or validation fails.
    """
    client = genai.Client(api_key=GEMINI_API_KEY)

    recs_json = json.dumps(
        [rec.model_dump() for rec in selected_recommendations], indent=2
    )
    user_prompt = build_itinerary_prompt(
        prefs.model_dump_json(indent=2), context_brief, recs_json
    )

    prompt = user_prompt
    last_error: Exception | None = None

    max_retries = MAX_AGENT_RETRIES + 1  # extra retry for large JSON responses
    for attempt in range(1, max_retries + 1):
        logger.info("Itinerary generation attempt %d/%d", attempt, MAX_AGENT_RETRIES)

        # Try primary model, fall back on 503
        raw_text = None
        for model in [GEMINI_MODEL, GEMINI_FALLBACK_MODEL]:
            try:
                response = await client.aio.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=ITINERARY_SYSTEM,
                        max_output_tokens=16384,
                        temperature=0.7,
                    ),
                )
                raw_text = response.text.strip()
                break
            except Exception as exc:
                if "503" in str(exc) or "UNAVAILABLE" in str(exc):
                    logger.warning("Itinerary: %s overloaded, trying fallback...", model)
                    import asyncio
                    await asyncio.sleep(1)
                    continue
                raise

        if not raw_text:
            raise RuntimeError("All Gemini models unavailable")

        try:
            # Strip markdown fences if present
            text = raw_text
            if text.startswith("```"):
                text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3].strip()

            data = json.loads(text)
            itinerary = Itinerary(**data)
            logger.info("Itinerary generated: '%s' with %d days", itinerary.trip_title, len(itinerary.days))
            return itinerary
        except Exception as exc:
            last_error = exc
            logger.warning("Itinerary parsing failed on attempt %d: %s", attempt, exc)
            prompt = (
                user_prompt
                + f"\n\nIMPORTANT: Your previous response failed: {exc}. "
                "This likely happened because the JSON was too long and got truncated. "
                "Keep descriptions SHORT (1 sentence each). Keep tips to one short sentence or null. "
                "Use 4-5 items per day max. Respond with ONLY valid JSON, no markdown fences."
            )

    raise RuntimeError(f"Itinerary generation failed after {max_retries} attempts. Last error: {last_error}")
