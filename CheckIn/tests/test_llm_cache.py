"""Taste-aware research cache: keys, margins, TTL, and agent integration."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import date

import pytest

import agents.base
import db
import llm_cache
import llm_client
from agents.accommodation import AccommodationAgent
from personalization import PROFILE_VIBES
from schemas import GroupType, TripPreferences

AGENT_KEY_ARGS = dict(
    agent_name="Accommodation Agent", operation="research", use_search=True
)


def _prefs(**overrides) -> TripPreferences:
    base = dict(
        destination="Kyoto",
        origin="Mumbai",
        start_date=date(2026, 10, 12),
        end_date=date(2026, 10, 18),
        budget_amount=3200,
        currency="USD",
        vibes=["culture", "food"],
        group_type=GroupType.COUPLE,
        num_travelers=2,
    )
    base.update(overrides)
    return TripPreferences(**base)


def _taste(weights: dict | None = None, pace: float = 0.5, dealbreakers=()) -> dict:
    vibe_weights = {vibe: 0.05 for vibe in PROFILE_VIBES}
    vibe_weights.update(weights or {})
    return {
        "vibe_weights": vibe_weights,
        "pace_score": pace,
        "dealbreakers": list(dealbreakers),
        "dietary_requirements": [],
    }


def _candidates(count: int = 5, constraint_tags: list[str] | None = None) -> dict:
    recommendations = []
    for index in range(count):
        rec = {
            "name": f"Hotel {index}",
            "category": "hotel",
            "description": "A calm machiya-style stay near the old town.",
            "reasoning": "Close to the cultural sights this couple asked for.",
            "estimated_cost": "$120-$180 per night",
            "cost_min": 120,
            "cost_max": 180,
            "rating": 4.0 + index * 0.1,
            "review_count": 1200,
            "location": "Gion",
            "image_search_query": "kyoto machiya hotel",
            "vibe_tags": ["culture", "food"],
        }
        if constraint_tags:
            rec["constraint_tags"] = list(constraint_tags)
        recommendations.append(rec)
    return {"recommendations": recommendations}


def _fake_generate(*payloads):
    """Async generate_text stand-in serving payloads in order (last repeats)."""
    calls: list[dict] = []
    queue = list(payloads)

    async def fake(prompt, *args, **kwargs):
        calls.append(kwargs)
        payload = queue.pop(0) if len(queue) > 1 else queue[0]
        text = payload if isinstance(payload, str) else json.dumps(payload)
        if kwargs.get("return_meta"):
            return text, {"model": "claude-opus-5", "failover": False}
        return text

    return fake, calls


@pytest.fixture(autouse=True)
def scratch_cache_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.dispose_engine()
    db.init_db()
    llm_cache.reset_research_cache()
    yield
    llm_cache.reset_research_cache()
    db.dispose_engine()


def test_hit_within_margin_and_exact_tuple():
    key, facts = llm_cache.exact_request_key(_prefs(), **AGENT_KEY_ARGS)
    stored = llm_cache.build_taste_vector(_taste({"culture": 0.4, "food": 0.3}))
    cache = llm_cache.get_research_cache()
    cache.store(key, facts, stored, [{"name": "Hotel 0"}], model="claude-opus-5")

    current = llm_cache.build_taste_vector(_taste({"culture": 0.38, "food": 0.32}))
    assert llm_cache.cosine_similarity(stored, current) >= llm_cache.CACHE_TASTE_MARGIN

    hit = cache.lookup(key, current)
    assert hit is not None
    assert hit.model == "claude-opus-5"
    assert hit.similarity >= 0.90
    assert hit.age_seconds >= 0
    assert hit.recommendations == [{"name": "Hotel 0"}]


def test_miss_just_outside_margin_boundary(monkeypatch):
    key, facts = llm_cache.exact_request_key(_prefs(), **AGENT_KEY_ARGS)
    stored = llm_cache.build_taste_vector(_taste({"culture": 0.5, "food": 0.4}))
    near = llm_cache.build_taste_vector(_taste({"culture": 0.48, "food": 0.42}))
    far = llm_cache.build_taste_vector(
        _taste({"culture": 0.0, "food": 0.0, "nightlife": 0.7, "shopping": 0.5}, pace=0.9)
    )

    sim_near = llm_cache.cosine_similarity(stored, near)
    sim_far = llm_cache.cosine_similarity(stored, far)
    assert sim_far < sim_near
    # Pin the margin between the two computed similarities so the boundary
    # verdicts are deterministic regardless of the configured default.
    monkeypatch.setattr(llm_cache, "CACHE_TASTE_MARGIN", (sim_far + sim_near) / 2)

    cache = llm_cache.get_research_cache()
    cache.store(key, facts, stored, [{"name": "Hotel 0"}], model="claude-opus-5")
    assert cache.lookup(key, near) is not None
    assert cache.lookup(key, far) is None


def test_exact_tuple_mismatch_is_miss():
    vector = llm_cache.build_taste_vector(_taste())
    key, facts = llm_cache.exact_request_key(_prefs(), **AGENT_KEY_ARGS)
    other_key, _ = llm_cache.exact_request_key(
        _prefs(destination="Osaka"), **AGENT_KEY_ARGS
    )
    assert other_key != key

    cache = llm_cache.get_research_cache()
    cache.store(key, facts, vector, [{"name": "Hotel 0"}], model="claude-opus-5")
    assert cache.lookup(other_key, vector) is None

    same_bucket, _ = llm_cache.exact_request_key(
        _prefs(budget_amount=1000), **AGENT_KEY_ARGS
    )
    near_budget, _ = llm_cache.exact_request_key(
        _prefs(budget_amount=1040), **AGENT_KEY_ARGS
    )
    far_budget, _ = llm_cache.exact_request_key(
        _prefs(budget_amount=2000), **AGENT_KEY_ARGS
    )
    assert same_bucket == near_budget
    assert same_bucket != far_budget

    swapped, _ = llm_cache.exact_request_key(
        _prefs(vibes=["food", "culture"]), **AGENT_KEY_ARGS
    )
    assert swapped == key


def test_ttl_expiry_is_miss(monkeypatch):
    key, facts = llm_cache.exact_request_key(_prefs(), **AGENT_KEY_ARGS)
    vector = llm_cache.build_taste_vector(_taste())
    cache = llm_cache.get_research_cache()
    cache.store(key, facts, vector, [{"name": "Hotel 0"}], model="claude-opus-5")

    time.sleep(0.01)
    monkeypatch.setattr(llm_cache, "CACHE_TTL_SECONDS", 0)
    assert cache.lookup(key, vector) is None


def test_agent_serves_cache_hit_with_metadata_stamp(monkeypatch):
    fake, calls = _fake_generate(_candidates())
    monkeypatch.setattr(agents.base, "generate_text", fake)
    agent = AccommodationAgent()
    prefs = _prefs()
    taste = _taste({"culture": 0.4, "food": 0.3})

    live = asyncio.run(agent.run(prefs, "context brief", user_taste=taste))
    assert len(calls) == 1
    assert all("cached" not in rec.metadata for rec in live.recommendations)

    served = asyncio.run(agent.run(prefs, "context brief", user_taste=taste))
    assert len(calls) == 1
    assert len(served.recommendations) == 3
    for rec in served.recommendations:
        assert rec.metadata["cached"] is True
        assert rec.metadata["cache_age_seconds"] >= 0
        assert rec.metadata["cache_similarity"] == pytest.approx(1.0)


def test_force_refresh_bypasses_lookup_but_stores(monkeypatch):
    fake, calls = _fake_generate(_candidates())
    monkeypatch.setattr(agents.base, "generate_text", fake)
    agent = AccommodationAgent()
    prefs = _prefs()
    taste = _taste()
    key, _ = llm_cache.exact_request_key(prefs, **AGENT_KEY_ARGS)

    asyncio.run(agent.run(prefs, "context brief", user_taste=taste))
    refreshed = asyncio.run(
        agent.run(prefs, "context brief", user_taste=taste, force_refresh=True)
    )

    assert len(calls) == 2
    assert all("cached" not in rec.metadata for rec in refreshed.recommendations)
    assert len(db.research_cache_fetch(key)) == 2


def test_never_caches_refusal_partial_or_capacity(monkeypatch):
    monkeypatch.setattr(agents.base, "MAX_AGENT_RETRIES", 1)
    agent = AccommodationAgent()
    prefs = _prefs()
    key, _ = llm_cache.exact_request_key(prefs, **AGENT_KEY_ARGS)

    async def refuse(prompt, *args, **kwargs):
        raise llm_client.LLMRefusalError()

    monkeypatch.setattr(agents.base, "generate_text", refuse)
    with pytest.raises(RuntimeError, match="attempts failed"):
        asyncio.run(agent.run(prefs, "context brief"))
    assert db.research_cache_fetch(key) == []

    invalid, _ = _fake_generate("this is not json")
    monkeypatch.setattr(agents.base, "generate_text", invalid)
    with pytest.raises(RuntimeError, match="attempts failed"):
        asyncio.run(agent.run(prefs, "context brief"))
    assert db.research_cache_fetch(key) == []

    async def busy(prompt, *args, **kwargs):
        raise llm_client.LLMCapacityError()

    monkeypatch.setattr(agents.base, "generate_text", busy)
    with pytest.raises(llm_client.LLMCapacityError):
        asyncio.run(agent.run(prefs, "context brief"))
    assert db.research_cache_fetch(key) == []


def test_hit_reranked_with_current_taste_veto_falls_through_to_live(monkeypatch):
    fake, calls = _fake_generate(
        _candidates(constraint_tags=["heights"]),
        _candidates(),
    )
    monkeypatch.setattr(agents.base, "generate_text", fake)
    agent = AccommodationAgent()
    prefs = _prefs()

    first = asyncio.run(agent.run(prefs, "context brief", user_taste=_taste()))
    assert len(calls) == 1
    assert all("cached" not in rec.metadata for rec in first.recommendations)

    # Same trip facts and an identical taste vector (dealbreakers do not feed
    # the vector), so the lookup hits — but re-ranking vetoes every cached
    # candidate and the agent must serve live instead.
    vetoing = _taste(dealbreakers=["heights"])
    second = asyncio.run(agent.run(prefs, "context brief", user_taste=vetoing))
    assert len(calls) == 2
    assert all("cached" not in rec.metadata for rec in second.recommendations)
    assert all("heights" not in rec.constraint_tags for rec in second.recommendations)


def test_cache_disabled_skips_lookup_and_store(monkeypatch):
    monkeypatch.setattr(llm_cache, "LLM_CACHE_ENABLED", False)
    fake, calls = _fake_generate(_candidates())
    monkeypatch.setattr(agents.base, "generate_text", fake)
    agent = AccommodationAgent()
    prefs = _prefs()
    key, _ = llm_cache.exact_request_key(prefs, **AGENT_KEY_ARGS)

    result = asyncio.run(agent.run(prefs, "context brief", user_taste=_taste()))
    assert len(result.recommendations) == 3
    assert len(calls) == 1
    assert db.research_cache_fetch(key) == []
    assert llm_cache.get_cache_stats()["enabled"] is False


def test_cache_errors_never_break_research(monkeypatch):
    class BrokenBackend:
        def fetch(self, exact_key):
            raise RuntimeError("backend down")

        def put(self, *args, **kwargs):
            raise RuntimeError("backend down")

    llm_cache.reset_research_cache(BrokenBackend())
    fake, calls = _fake_generate(_candidates())
    monkeypatch.setattr(agents.base, "generate_text", fake)
    agent = AccommodationAgent()

    result = asyncio.run(agent.run(_prefs(), "context brief", user_taste=_taste()))
    assert result.agent_name == "Accommodation Agent"
    assert len(result.recommendations) == 3
    assert len(calls) == 1
    assert llm_cache.get_cache_stats()["errors"] >= 2
