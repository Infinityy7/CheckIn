"""Base class all the specialist agents share."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from config import MAX_AGENT_RETRIES
from llm_client import LLMCapacityError, generate_text, is_fatal_error, parse_json_text
from ranking import rank_recommendations
from schemas import AgentResult, Recommendation, TripPreferences

logger = logging.getLogger(__name__)

# ask for 8, rank them ourselves, keep the best 3 — the bigger pool
# gives the taste ranker real choices to sort
CANDIDATES_REQUESTED = 8
RESULTS_RETURNED = 3
MIN_VALID_CANDIDATES = 3

RETRY_CORRECTION = (
    "\n\nIMPORTANT: Your previous response was not valid JSON or did not match "
    "the required schema. Please respond with ONLY the JSON object — no markdown "
    "fences, no commentary. Make sure all fields are present, ratings are numbers "
    "between 0 and 5, and cost_min/cost_max/review_count are plain numbers."
)


class BaseAgent(ABC):
    """Subclasses supply prompts; this class owns validation and ranking."""

    @property
    @abstractmethod
    def agent_name(self) -> str:
        """Name used in logs and results."""

    @property
    @abstractmethod
    def system_prompt(self) -> str:
        """The agent's persona prompt."""

    @abstractmethod
    def build_user_prompt(self, prefs: TripPreferences, context_brief: str) -> str:
        """Build the user message for this agent."""

    async def run(
        self,
        prefs: TripPreferences,
        context_brief: str,
        user_taste: dict | None = None,
        cotraveller_tastes: list[dict] | None = None,
    ) -> AgentResult:
        """Call the LLM, validate the output, rank it, return the top 3."""
        user_prompt = self.build_user_prompt(prefs, context_brief)
        last_error: Exception | None = None

        for attempt in range(1, MAX_AGENT_RETRIES + 1):
            prompt = user_prompt
            if attempt > 1:
                # tell the model what went wrong last time
                prompt = user_prompt + RETRY_CORRECTION

            try:
                logger.info(
                    "[%s] attempt %d/%d — calling OpenAI with web search",
                    self.agent_name, attempt, MAX_AGENT_RETRIES,
                )
                raw_text = await generate_text(
                    prompt,
                    system_instruction=self.system_prompt,
                    use_search=True,
                    temperature=0.3,
                    prefer_fallback=attempt > 1,
                    json_mode=True,
                    workload="research",
                    operation=self.agent_name,
                )
                candidates = self._parse_and_validate(raw_text)
            except Exception as exc:
                if isinstance(exc, LLMCapacityError):
                    raise  # both configured models share this local bulkhead
                if is_fatal_error(exc):
                    raise  # bad API key etc, no point retrying
                last_error = exc
                logger.warning(
                    "[%s] attempt %d failed: %s",
                    self.agent_name,
                    attempt,
                    type(exc).__name__,
                )
                continue

            ranked = rank_recommendations(candidates, prefs, user_taste, cotraveller_tastes)
            if len(ranked) < MIN_VALID_CANDIDATES:
                last_error = ValueError(
                    f"Only {len(ranked)} candidates survived hard constraints "
                    f"(need {MIN_VALID_CANDIDATES})"
                )
                logger.warning("[%s] attempt %d failed: %s", self.agent_name, attempt, last_error)
                continue
            top = ranked[:RESULTS_RETURNED]
            logger.info(
                "[%s] %d valid candidates, kept top %d (best: '%s', score %.3f)",
                self.agent_name, len(ranked), len(top), top[0].name, top[0].score,
            )
            return AgentResult(agent_name=self.agent_name, recommendations=top)

        raise RuntimeError(
            f"[{self.agent_name}] all {MAX_AGENT_RETRIES} attempts failed. Last error: {last_error}"
        )

    def _parse_and_validate(self, raw_text: str) -> list[Recommendation]:
        """Turn the LLM's JSON into Recommendation objects.

        Bad items get dropped one by one instead of failing the whole batch.
        Only errors out if fewer than 3 good ones survive.
        """
        data = parse_json_text(raw_text)

        if isinstance(data, list):
            raw_recs = data
        elif isinstance(data, dict):
            raw_recs = data.get("recommendations")
        else:
            raw_recs = None

        if not isinstance(raw_recs, list):
            raise ValueError("Response JSON did not contain a recommendations list")

        valid: list[Recommendation] = []
        seen_names: set[str] = set()

        for item in raw_recs:
            try:
                rec = Recommendation(**item)
            except Exception as exc:
                logger.warning("[%s] dropping invalid candidate: %s", self.agent_name, exc)
                continue

            # skip repeats of the same place
            name_key = rec.name.strip().lower()
            if name_key in seen_names:
                logger.info("[%s] dropping duplicate candidate '%s'", self.agent_name, rec.name)
                continue
            seen_names.add(name_key)
            valid.append(rec)

        if len(valid) < MIN_VALID_CANDIDATES:
            raise ValueError(
                f"Only {len(valid)} valid candidates (need at least {MIN_VALID_CANDIDATES})"
            )
        return valid
