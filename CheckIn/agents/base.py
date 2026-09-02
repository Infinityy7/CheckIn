"""Base class all the specialist agents share."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from config import MAX_AGENT_RETRIES
from llm_cache import (
    CacheHit,
    build_taste_vector,
    exact_request_key,
    get_research_cache,
)
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

    # Every specialist produces exactly one category, so a candidate that
    # arrives without one is repairable instead of a dropped 16k-token answer.
    default_category: str | None = None

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

    async def prepare_context(self, prefs: TripPreferences, context_brief: str) -> str:
        """Hook for subclasses to enrich the shared brief before prompting."""
        return context_brief

    cache_skip_reason: str = ""

    def cacheable_result(self) -> bool:
        """Hook: return False when this run's result must not be stored."""
        return True

    async def run(
        self,
        prefs: TripPreferences,
        context_brief: str,
        user_taste: dict | None = None,
        cotraveller_tastes: list[dict] | None = None,
        *,
        force_refresh: bool = False,
    ) -> AgentResult:
        """Call the LLM (or serve a taste-similar cached result), validate, rank."""
        last_error: Exception | None = None

        cache = get_research_cache()
        exact_key = None
        request_facts = None
        taste_vector = None
        if cache.enabled:
            exact_key, request_facts = exact_request_key(
                prefs,
                agent_name=self.agent_name,
                operation="research",
                use_search=True,
                context_brief=context_brief,
                user_taste=user_taste,
                cotraveller_tastes=cotraveller_tastes,
            )
            taste_vector = build_taste_vector(user_taste, cotraveller_tastes)
            if not force_refresh:
                hit = cache.lookup(exact_key, taste_vector)
                if hit is not None:
                    served = self._from_cache(hit, prefs, user_taste, cotraveller_tastes)
                    if served is not None:
                        logger.info(
                            "[%s] served from cache age=%ds similarity=%.3f",
                            self.agent_name, int(hit.age_seconds), hit.similarity,
                        )
                        return served

        # runs after the cache lookup so a hit never pays for context enrichment
        context_brief = await self.prepare_context(prefs, context_brief)
        user_prompt = self.build_user_prompt(prefs, context_brief)

        for attempt in range(1, MAX_AGENT_RETRIES + 1):
            prompt = user_prompt
            if attempt > 1:
                # tell the model what went wrong last time
                prompt = user_prompt + RETRY_CORRECTION

            try:
                logger.info(
                    "[%s] attempt %d/%d — calling Claude with web search",
                    self.agent_name, attempt, MAX_AGENT_RETRIES,
                )
                raw_text, call_meta = await generate_text(
                    prompt,
                    system_instruction=self.system_prompt,
                    use_search=True,
                    max_tokens=16000,
                    prefer_fallback=attempt > 1,
                    json_mode=True,
                    effort="high",
                    workload="research",
                    operation=self.agent_name,
                    return_meta=True,
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
            if cache.enabled and exact_key is not None:
                if self.cacheable_result():
                    cache.store(
                        exact_key,
                        request_facts or {},
                        taste_vector or [],
                        [rec.model_dump(mode="json") for rec in candidates],
                        model=call_meta.get("model", ""),
                    )
                else:
                    logger.info(
                        "[%s] result not cached: %s", self.agent_name, self.cache_skip_reason
                    )
            return AgentResult(agent_name=self.agent_name, recommendations=top)

        raise RuntimeError(
            f"[{self.agent_name}] all {MAX_AGENT_RETRIES} attempts failed. Last error: {last_error}"
        )

    def _from_cache(
        self,
        hit: CacheHit,
        prefs: TripPreferences,
        user_taste: dict | None,
        cotraveller_tastes: list[dict] | None,
    ) -> AgentResult | None:
        """Re-validate and re-rank a cached candidate pool with the current taste."""
        candidates: list[Recommendation] = []
        for item in hit.recommendations:
            try:
                candidates.append(Recommendation(**item))
            except Exception:
                return None
        ranked = rank_recommendations(candidates, prefs, user_taste, cotraveller_tastes)
        if len(ranked) < MIN_VALID_CANDIDATES:
            return None
        top = ranked[:RESULTS_RETURNED]
        age = int(hit.age_seconds)
        for rec in top:
            rec.metadata = {
                **rec.metadata,
                "cached": True,
                "cache_age_seconds": age,
                "cache_similarity": round(hit.similarity, 4),
            }
        return AgentResult(agent_name=self.agent_name, recommendations=top)

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
            if isinstance(item, dict) and self.default_category:
                category = item.get("category")
                if category and category != self.default_category:
                    logger.info(
                        "[%s] overriding category %r with '%s'",
                        self.agent_name, category, self.default_category,
                    )
                item["category"] = self.default_category
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
