"""Base class for all TravelBuddy specialist agents."""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod

from google import genai
from google.genai import types

from config import GEMINI_API_KEY, GEMINI_MODEL, GEMINI_FALLBACK_MODEL, MAX_AGENT_RETRIES
from schemas import AgentResult, Recommendation, TripPreferences

logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    """Abstract base for a specialist travel research agent.

    Subclasses provide the system prompt, user prompt builder, agent name,
    and recommendation category. The base class handles the Gemini API call,
    Google Search grounding, JSON parsing, Pydantic validation, and retry logic.
    """

    def __init__(self) -> None:
        self._client = genai.Client(api_key=GEMINI_API_KEY)

    # --- subclass interface ---

    @property
    @abstractmethod
    def agent_name(self) -> str:
        """Human-readable name used in logs and results."""

    @property
    @abstractmethod
    def system_prompt(self) -> str:
        """System prompt that defines this agent's persona."""

    @abstractmethod
    def build_user_prompt(self, prefs: TripPreferences, context_brief: str) -> str:
        """Build the user message for this agent."""

    # --- public API ---

    async def run(self, prefs: TripPreferences, context_brief: str) -> AgentResult:
        """Execute the agent: call Gemini with Google Search, parse, validate, retry on failure."""
        user_prompt = self.build_user_prompt(prefs, context_brief)
        last_error: Exception | None = None

        for attempt in range(1, MAX_AGENT_RETRIES + 1):
            try:
                logger.info(
                    "[%s] attempt %d/%d — calling Gemini with Google Search",
                    self.agent_name, attempt, MAX_AGENT_RETRIES,
                )
                raw_text = await self._call_gemini(user_prompt, attempt)
                recommendations = self._parse_and_validate(raw_text)
                logger.info("[%s] successfully produced %d recommendations", self.agent_name, len(recommendations))
                return AgentResult(agent_name=self.agent_name, recommendations=recommendations)
            except Exception as exc:
                last_error = exc
                logger.warning("[%s] attempt %d failed: %s", self.agent_name, attempt, exc)

        raise RuntimeError(
            f"[{self.agent_name}] all {MAX_AGENT_RETRIES} attempts failed. Last error: {last_error}"
        )

    # --- internals ---

    async def _call_gemini(self, user_prompt: str, attempt: int) -> str:
        """Send the request to Gemini with Google Search grounding enabled.

        Falls back to GEMINI_FALLBACK_MODEL on 503 (overloaded).
        """
        import asyncio

        prompt = user_prompt

        if attempt > 1:
            prompt += (
                "\n\nIMPORTANT: Your previous response was not valid JSON or did not match "
                "the required schema. Please respond with ONLY the JSON array — no markdown "
                "fences, no commentary. Make sure all fields are present and ratings are "
                "numbers between 0 and 5."
            )

        config = types.GenerateContentConfig(
            system_instruction=self.system_prompt,
            tools=[types.Tool(google_search=types.GoogleSearch())],
            max_output_tokens=4096,
            temperature=0.7,
        )

        # Try primary model, fall back on 503
        for model in [GEMINI_MODEL, GEMINI_FALLBACK_MODEL]:
            try:
                response = await self._client.aio.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=config,
                )
                text = response.text
                if not text:
                    raise ValueError("Gemini returned no text content")
                return text
            except Exception as exc:
                if "503" in str(exc) or "UNAVAILABLE" in str(exc):
                    logger.warning("[%s] %s overloaded, trying fallback...", self.agent_name, model)
                    await asyncio.sleep(1)
                    continue
                raise

        raise RuntimeError("All Gemini models unavailable")

    def _parse_and_validate(self, raw_text: str) -> list[Recommendation]:
        """Parse raw Gemini output into validated Recommendation objects."""
        # Strip markdown fences if present
        text = raw_text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3].strip()

        data = json.loads(text)

        raw_recs = data if isinstance(data, list) else data.get("recommendations", data)
        if not isinstance(raw_recs, list) or len(raw_recs) != 3:
            raise ValueError(f"Expected exactly 3 recommendations, got {len(raw_recs) if isinstance(raw_recs, list) else type(raw_recs)}")

        return [Recommendation(**rec) for rec in raw_recs]
