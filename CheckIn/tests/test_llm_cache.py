"""Taste-aware research cache: keys, margins, TTL, and agent integration."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from datetime import date, datetime, timedelta, timezone

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


def _key(prefs: TripPreferences, **overrides) -> tuple[str, dict]:
    kwargs = dict(
        AGENT_KEY_ARGS,
        context_brief="context brief",
        user_taste=None,
        cotraveller_tastes=None,
    )
    kwargs.update(overrides)
    return llm_cache.exact_request_key(prefs, **kwargs)


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
    key, facts = _key(_prefs())
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
    key, facts = _key(_prefs())
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
    key, facts = _key(_prefs())
    other_key, _ = _key(_prefs(destination="Osaka"))
    assert other_key != key

    cache = llm_cache.get_research_cache()
    cache.store(key, facts, vector, [{"name": "Hotel 0"}], model="claude-opus-5")
    assert cache.lookup(other_key, vector) is None

    same_bucket, _ = _key(_prefs(budget_amount=1000))
    near_budget, _ = _key(_prefs(budget_amount=1040))
    far_budget, _ = _key(_prefs(budget_amount=2000))
    assert same_bucket == near_budget
    assert same_bucket != far_budget

    swapped, _ = _key(_prefs(vibes=["food", "culture"]))
    assert swapped == key


def test_ttl_expiry_is_miss(monkeypatch):
    key, facts = _key(_prefs())
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
    key, _ = _key(prefs, user_taste=taste)

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
    key, _ = _key(prefs)

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
    key, _ = _key(prefs, user_taste=_taste())

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

        def prune(self, expire_before):
            raise RuntimeError("backend down")

    llm_cache.reset_research_cache(BrokenBackend())
    fake, calls = _fake_generate(_candidates())
    monkeypatch.setattr(agents.base, "generate_text", fake)
    agent = AccommodationAgent()

    result = asyncio.run(agent.run(_prefs(), "context brief", user_taste=_taste()))
    assert result.agent_name == "Accommodation Agent"
    assert len(result.recommendations) == 3
    assert len(calls) == 1
    assert llm_cache.get_cache_stats()["errors"] >= 3


def test_backend_without_prune_is_tolerated(monkeypatch):
    class MemoryBackend:
        def __init__(self):
            self.rows: dict[str, list[dict]] = {}

        def fetch(self, exact_key):
            return list(self.rows.get(exact_key, []))

        def put(self, exact_key, taste_vector, request_facts, payload, model, expire_before):
            self.rows.setdefault(exact_key, []).append({
                "taste_vector": taste_vector,
                "payload": payload,
                "model": model,
                "created_at": datetime.now(timezone.utc).isoformat(),
            })

    cache = llm_cache.reset_research_cache(MemoryBackend())
    key, facts = _key(_prefs())
    vector = llm_cache.build_taste_vector(_taste())
    cache.store(key, facts, vector, [{"name": "Hotel 0"}], model="m")
    assert cache.lookup(key, vector) is not None
    assert llm_cache.get_cache_stats()["errors"] == 0
    assert llm_cache.get_cache_stats()["pruned"] == 0


def test_same_trip_facts_different_profile_prose_never_share_reasoning(monkeypatch):
    fake, calls = _fake_generate(_candidates())
    monkeypatch.setattr(agents.base, "generate_text", fake)
    agent = AccommodationAgent()
    prefs = _prefs()
    taste = _taste({"culture": 0.4, "food": 0.3})

    user_a_brief = "Profile: Priya, vegetarian, loves quiet temples, fears heights."
    user_b_brief = "Profile: Marco, loves izakaya crawls and karaoke until 2am."
    key_a, facts_a = _key(prefs, context_brief=user_a_brief, user_taste=taste)
    key_b, facts_b = _key(prefs, context_brief=user_b_brief, user_taste=taste)
    assert key_a != key_b
    assert facts_a["prompt_fingerprint"] != facts_b["prompt_fingerprint"]
    for facts in (facts_a, facts_b):
        assert "Priya" not in json.dumps(facts)
        assert "Marco" not in json.dumps(facts)

    first = asyncio.run(agent.run(prefs, user_a_brief, user_taste=taste))
    assert len(calls) == 1
    second = asyncio.run(agent.run(prefs, user_b_brief, user_taste=taste))
    assert len(calls) == 2
    assert all("cached" not in rec.metadata for rec in first.recommendations)
    assert all("cached" not in rec.metadata for rec in second.recommendations)
    assert len(db.research_cache_fetch(key_a)) == 1
    assert len(db.research_cache_fetch(key_b)) == 1

    third = asyncio.run(agent.run(prefs, user_a_brief, user_taste=taste))
    assert len(calls) == 2
    assert all(rec.metadata["cached"] is True for rec in third.recommendations)


def test_profile_version_change_invalidates_same_users_entry(monkeypatch):
    fake, calls = _fake_generate(_candidates())
    monkeypatch.setattr(agents.base, "generate_text", fake)
    agent = AccommodationAgent()
    prefs = _prefs()
    brief = "Profile: quiet culture-first couple."

    version_1 = {**_taste({"culture": 0.4}), "profile_version": 1}
    version_2 = {**_taste({"culture": 0.4}), "profile_version": 2}
    assert llm_cache.build_taste_vector(version_1) == llm_cache.build_taste_vector(version_2)

    asyncio.run(agent.run(prefs, brief, user_taste=version_1))
    served = asyncio.run(agent.run(prefs, brief, user_taste=version_1))
    assert len(calls) == 1
    assert all(rec.metadata["cached"] is True for rec in served.recommendations)

    refreshed = asyncio.run(agent.run(prefs, brief, user_taste=version_2))
    assert len(calls) == 2
    assert all("cached" not in rec.metadata for rec in refreshed.recommendations)

    rewritten = asyncio.run(agent.run(prefs, brief + " Now travelling with a toddler.", user_taste=version_2))
    assert len(calls) == 3
    assert all("cached" not in rec.metadata for rec in rewritten.recommendations)


def test_supplier_mode_change_invalidates_transport_entries(monkeypatch):
    import inventory.service as inventory_service
    from agents.transport import TransportAgent
    from inventory.models import SourceMode

    provider = inventory_service.get_inventory_service().provider
    monkeypatch.setattr(provider, "source_mode", SourceMode.UNAVAILABLE)
    transport_args = dict(agent_name="Transport Agent")
    before_key, before_facts = _key(_prefs(), **transport_args)
    assert before_facts["supplier_mode"] == "unavailable"

    monkeypatch.setattr(provider, "source_mode", SourceMode.LIVE)
    after_key, after_facts = _key(_prefs(), **transport_args)
    assert after_facts["supplier_mode"] == "live"
    assert after_key != before_key

    async def no_briefing(_prefs):
        return None

    monkeypatch.setattr("agents.transport.build_flight_briefing", no_briefing)
    fake, calls = _fake_generate(_candidates())
    monkeypatch.setattr(agents.base, "generate_text", fake)
    agent = TransportAgent()
    prefs = _prefs()

    monkeypatch.setattr(provider, "source_mode", SourceMode.UNAVAILABLE)
    asyncio.run(agent.run(prefs, "context brief", user_taste=_taste()))
    served = asyncio.run(agent.run(prefs, "context brief", user_taste=_taste()))
    assert len(calls) == 1
    assert all(rec.metadata["cached"] is True for rec in served.recommendations)

    monkeypatch.setattr(provider, "source_mode", SourceMode.LIVE)
    refreshed = asyncio.run(agent.run(prefs, "context brief", user_taste=_taste()))
    assert len(calls) == 2
    assert all("cached" not in rec.metadata for rec in refreshed.recommendations)


def _transport_agent_with_briefing(monkeypatch, briefing_text: str | None):
    from agents.transport import TransportAgent

    async def briefing(_prefs):
        return briefing_text

    monkeypatch.setattr("agents.transport.build_flight_briefing", briefing)
    fake, calls = _fake_generate(_candidates())
    monkeypatch.setattr(agents.base, "generate_text", fake)
    return TransportAgent(), calls


def _set_supplier_mode(monkeypatch, mode):
    import inventory.service as inventory_service

    monkeypatch.setattr(inventory_service.get_inventory_service().provider, "source_mode", mode)


@pytest.mark.parametrize("mode", ["live", "test"])
def test_transport_estimates_are_not_cached_when_briefing_expected_but_missing(
    monkeypatch, caplog, mode
):
    from inventory.models import SourceMode

    _set_supplier_mode(monkeypatch, SourceMode(mode))
    agent, calls = _transport_agent_with_briefing(monkeypatch, None)
    prefs, taste = _prefs(), _taste()
    key, facts = _key(prefs, agent_name="Transport Agent", user_taste=taste)
    assert facts["supplier_mode"] == mode

    with caplog.at_level(logging.INFO, logger="agents.base"):
        result = asyncio.run(agent.run(prefs, "context brief", user_taste=taste))

    assert len(result.recommendations) == 3
    assert len(calls) == 1
    assert db.research_cache_fetch(key) == []
    assert llm_cache.get_cache_stats()["stores"] == 0
    assert any("result not cached" in record.getMessage() for record in caplog.records)

    again = asyncio.run(agent.run(prefs, "context brief", user_taste=taste))
    assert len(calls) == 2
    assert all("cached" not in rec.metadata for rec in again.recommendations)


def test_transport_result_is_cached_when_briefing_present(monkeypatch):
    from inventory.models import SourceMode

    _set_supplier_mode(monkeypatch, SourceMode.LIVE)
    agent, calls = _transport_agent_with_briefing(
        monkeypatch, "## Supplier flight offers (CheckIn inventory API)\n- offer"
    )
    prefs, taste = _prefs(), _taste()
    key, _ = _key(prefs, agent_name="Transport Agent", user_taste=taste)

    asyncio.run(agent.run(prefs, "context brief", user_taste=taste))
    assert len(db.research_cache_fetch(key)) == 1

    served = asyncio.run(agent.run(prefs, "context brief", user_taste=taste))
    assert len(calls) == 1
    assert all(rec.metadata["cached"] is True for rec in served.recommendations)


def test_transport_estimates_are_cached_when_inventory_unavailable(monkeypatch):
    from inventory.models import SourceMode

    _set_supplier_mode(monkeypatch, SourceMode.UNAVAILABLE)
    agent, calls = _transport_agent_with_briefing(monkeypatch, None)
    prefs, taste = _prefs(), _taste()
    key, facts = _key(prefs, agent_name="Transport Agent", user_taste=taste)
    assert facts["supplier_mode"] == "unavailable"

    asyncio.run(agent.run(prefs, "context brief", user_taste=taste))
    assert len(db.research_cache_fetch(key)) == 1

    served = asyncio.run(agent.run(prefs, "context brief", user_taste=taste))
    assert len(calls) == 1
    assert all(rec.metadata["cached"] is True for rec in served.recommendations)


def test_old_key_version_rows_never_match():
    key, facts = _key(_prefs())
    assert facts["key_version"] == llm_cache.KEY_VERSION == 2
    assert facts["prompt_schema_version"] == llm_cache.PROMPT_SCHEMA_VERSION

    legacy_facts = {
        name: value for name, value in facts.items()
        if name not in {"prompt_schema_version", "supplier_mode", "prompt_fingerprint"}
    }
    legacy_facts["key_version"] = 1
    legacy_key = hashlib.sha256(
        json.dumps(legacy_facts, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert legacy_key != key

    vector = llm_cache.build_taste_vector(_taste())
    cache = llm_cache.get_research_cache()
    cache.store(legacy_key, legacy_facts, vector, [{"name": "Hotel 0"}], model="m")
    assert cache.lookup(legacy_key, vector) is not None
    assert cache.lookup(key, vector) is None


def _insert_cache_row(exact_key: str, created_at: datetime) -> None:
    from sqlalchemy.orm import Session

    with Session(db._connect()) as session, session.begin():
        session.add(db.LLMResearchCache(
            exact_key=exact_key,
            taste_vector=[1.0],
            request_facts={"key_version": 1},
            payload=[{"name": "stale"}],
            model="m",
            created_at=created_at,
        ))


def test_store_prunes_expired_rows_under_every_key():
    stale = datetime.now(timezone.utc) - timedelta(seconds=llm_cache.CACHE_TTL_SECONDS + 3600)
    _insert_cache_row("stale-key-a", stale)
    _insert_cache_row("stale-key-b", stale)
    _insert_cache_row("fresh-key", datetime.now(timezone.utc))
    assert len(db.research_cache_fetch("stale-key-a")) == 1
    assert len(db.research_cache_fetch("stale-key-b")) == 1

    key, facts = _key(_prefs())
    cache = llm_cache.get_research_cache()
    cache.store(key, facts, llm_cache.build_taste_vector(_taste()), [{"name": "Hotel 0"}], model="m")

    assert db.research_cache_fetch("stale-key-a") == []
    assert db.research_cache_fetch("stale-key-b") == []
    assert len(db.research_cache_fetch("fresh-key")) == 1
    assert len(db.research_cache_fetch(key)) == 1
    assert llm_cache.get_cache_stats()["pruned"] == 2


def test_lookup_prunes_at_most_once_per_interval(monkeypatch):
    stale = datetime.now(timezone.utc) - timedelta(seconds=llm_cache.CACHE_TTL_SECONDS + 3600)
    _insert_cache_row("stale-key-a", stale)
    cache = llm_cache.get_research_cache()
    vector = llm_cache.build_taste_vector(_taste())

    assert cache.lookup("some-key", vector) is None
    assert db.research_cache_fetch("stale-key-a") == []
    assert cache.pruned == 1

    _insert_cache_row("stale-key-b", stale)
    assert cache.lookup("some-key", vector) is None
    assert len(db.research_cache_fetch("stale-key-b")) == 1

    monkeypatch.setattr(cache, "_last_prune_at", cache._last_prune_at - 61)
    assert cache.lookup("some-key", vector) is None
    assert db.research_cache_fetch("stale-key-b") == []
    assert cache.pruned == 2


def test_research_cache_prune_returns_deleted_rowcount():
    stale = datetime.now(timezone.utc) - timedelta(hours=7)
    _insert_cache_row("a", stale)
    _insert_cache_row("b", stale)
    _insert_cache_row("c", datetime.now(timezone.utc))

    assert db.research_cache_prune(datetime.now(timezone.utc) - timedelta(hours=6)) == 2
    assert db.research_cache_fetch("a") == []
    assert len(db.research_cache_fetch("c")) == 1
