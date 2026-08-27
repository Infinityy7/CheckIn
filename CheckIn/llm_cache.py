"""Taste-aware research cache: exact on trip facts, fuzzy on taste."""

from __future__ import annotations

import hashlib
import json
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

import db
from config import (
    CACHE_BUDGET_BUCKET_PCT,
    CACHE_TASTE_MARGIN,
    CACHE_TTL_SECONDS,
    LLM_CACHE_ENABLED,
)
from personalization import PROFILE_VIBES

logger = logging.getLogger(__name__)

_PACE_LABELS = {
    "slow": 0.15,
    "moderate": 0.5,
    "balanced": 0.5,
    "packed": 0.85,
    "fast": 0.85,
}


def _vibe_weights(taste: dict | None) -> list[float]:
    taste = taste or {}
    weights = taste.get("vibe_weights")
    if not isinstance(weights, dict):
        likes = taste.get("likes") if isinstance(taste.get("likes"), dict) else {}
        weights = {vibe: max(0.0, float(likes.get(vibe, 0.0))) for vibe in PROFILE_VIBES}
    values = []
    for vibe in PROFILE_VIBES:
        try:
            values.append(max(0.0, float(weights.get(vibe, 0.0))))
        except (TypeError, ValueError):
            values.append(0.0)
    total = sum(values)
    if total <= 0:
        return [1.0 / len(PROFILE_VIBES)] * len(PROFILE_VIBES)
    return [value / total for value in values]


def _pace_score(taste: dict | None) -> float:
    taste = taste or {}
    pace = taste.get("pace_score")
    if isinstance(pace, (int, float)):
        return max(0.0, min(1.0, float(pace)))
    return _PACE_LABELS.get(str(taste.get("pace", "")).lower(), 0.5)


def _person_vector(taste: dict | None) -> list[float]:
    return [*_vibe_weights(taste), _pace_score(taste)]


def build_taste_vector(
    user_taste: dict | None,
    cotraveller_tastes: list[dict] | None = None,
) -> list[float]:
    """Blend co-traveller tastes 0.6/0.4 the same way the ranker does."""
    user = _person_vector(user_taste)
    others = [_person_vector(taste) for taste in (cotraveller_tastes or []) if taste]
    if not others:
        return user
    mean_co = [sum(values) / len(others) for values in zip(*others)]
    return [0.6 * u + 0.4 * c for u, c in zip(user, mean_co)]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _budget_bucket(amount: float) -> int:
    pct = max(float(CACHE_BUDGET_BUCKET_PCT), 0.01) / 100.0
    return int(math.floor(math.log(max(float(amount), 0.01)) / math.log(1.0 + pct)))


def exact_request_key(
    prefs: Any,
    *,
    agent_name: str,
    operation: str,
    use_search: bool,
) -> tuple[str, dict]:
    facts = {
        "key_version": 1,
        "destination": prefs.destination.strip().lower(),
        "origin": prefs.origin.strip().lower(),
        "start_date": prefs.start_date.isoformat(),
        "end_date": prefs.end_date.isoformat(),
        "currency": prefs.currency,
        "vibes": sorted(set(prefs.vibes)),
        "group_type": getattr(prefs.group_type, "value", str(prefs.group_type)),
        "num_travelers": prefs.num_travelers,
        "cotraveller_count": len(prefs.cotravellers)
        + len(getattr(prefs, "cotraveller_usernames", []) or []),
        "agent_name": agent_name,
        "operation": operation,
        "use_search": bool(use_search),
        "budget_bucket": _budget_bucket(prefs.budget_amount),
    }
    key = hashlib.sha256(
        json.dumps(facts, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return key, facts


@dataclass
class CacheHit:
    recommendations: list[dict]
    age_seconds: float
    similarity: float
    model: str


class ResearchCacheBackend(Protocol):
    def fetch(self, exact_key: str) -> list[dict]: ...

    def put(
        self,
        exact_key: str,
        taste_vector: list[float],
        request_facts: dict,
        payload: list[dict],
        model: str,
        expire_before: datetime,
    ) -> None: ...


class SqlResearchCacheBackend:
    def fetch(self, exact_key: str) -> list[dict]:
        return db.research_cache_fetch(exact_key)

    def put(
        self,
        exact_key: str,
        taste_vector: list[float],
        request_facts: dict,
        payload: list[dict],
        model: str,
        expire_before: datetime,
    ) -> None:
        db.research_cache_put(
            exact_key, taste_vector, request_facts, payload, model,
            expire_before=expire_before,
        )


def _parse_created_at(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


class ResearchCache:
    def __init__(self, backend: ResearchCacheBackend | None = None) -> None:
        self._backend = backend or SqlResearchCacheBackend()
        self.hits = 0
        self.misses = 0
        self.stores = 0
        self.errors = 0

    @property
    def enabled(self) -> bool:
        return LLM_CACHE_ENABLED

    def lookup(self, exact_key: str, taste_vector: list[float]) -> CacheHit | None:
        if not self.enabled:
            return None
        try:
            rows = self._backend.fetch(exact_key)
        except Exception as exc:
            self.errors += 1
            logger.warning("research cache lookup failed: %s", type(exc).__name__)
            return None
        now = datetime.now(timezone.utc)
        best: CacheHit | None = None
        for row in rows:
            created_at = _parse_created_at(row.get("created_at"))
            if created_at is None:
                continue
            age = (now - created_at).total_seconds()
            if age < 0 or age > CACHE_TTL_SECONDS:
                continue
            stored_vector = row.get("taste_vector") or []
            try:
                similarity = cosine_similarity(
                    [float(v) for v in stored_vector], taste_vector
                )
            except (TypeError, ValueError):
                continue
            if similarity < CACHE_TASTE_MARGIN:
                continue
            candidate = CacheHit(
                recommendations=row.get("payload") or [],
                age_seconds=age,
                similarity=similarity,
                model=str(row.get("model") or ""),
            )
            if (
                best is None
                or candidate.similarity > best.similarity
                or (
                    candidate.similarity == best.similarity
                    and candidate.age_seconds < best.age_seconds
                )
            ):
                best = candidate
        if best is None:
            self.misses += 1
        else:
            self.hits += 1
        return best

    def store(
        self,
        exact_key: str,
        request_facts: dict,
        taste_vector: list[float],
        recommendations: list[dict],
        model: str,
    ) -> None:
        if not self.enabled or not recommendations:
            return
        expire_before = datetime.now(timezone.utc) - timedelta(seconds=CACHE_TTL_SECONDS)
        try:
            self._backend.put(
                exact_key, taste_vector, request_facts, recommendations, model,
                expire_before,
            )
            self.stores += 1
        except Exception as exc:
            self.errors += 1
            logger.warning("research cache store failed: %s", type(exc).__name__)


_cache: ResearchCache | None = None


def get_research_cache() -> ResearchCache:
    global _cache
    if _cache is None:
        _cache = ResearchCache()
    return _cache


def reset_research_cache(backend: ResearchCacheBackend | None = None) -> ResearchCache:
    global _cache
    _cache = ResearchCache(backend)
    return _cache


def get_cache_stats() -> dict:
    cache = get_research_cache()
    return {
        "enabled": cache.enabled,
        "hits": cache.hits,
        "misses": cache.misses,
        "stores": cache.stores,
        "errors": cache.errors,
    }
