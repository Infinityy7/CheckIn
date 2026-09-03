"""Planning scope: field defaults and validation, scoped research, feasibility, prompts."""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import db
import feasibility
import itinerary as itinerary_module
import main
import profiles
from orchestrator import (
    AGENT_CATEGORIES,
    ALL_AGENT_NAMES,
    agents_for_scope,
    build_fallback_context_brief,
)
from schemas import (
    SCOPE_CATEGORIES,
    AgentResult,
    DayPlan,
    GroupType,
    Itinerary,
    ItineraryItem,
    Recommendation,
    TripPreferences,
    planning_scope_note,
)
from store import get_trip

CATEGORY_OF = {
    "Accommodation Agent": "hotel",
    "Activities Agent": "activity",
    "Restaurant Agent": "restaurant",
    "Transport Agent": "transport",
}


def payload(**overrides) -> dict:
    start = datetime.now(timezone.utc).date() + timedelta(days=60)
    body = {
        "destination": "Kyoto",
        "origin": "Mumbai",
        "start_date": start.isoformat(),
        "end_date": (start + timedelta(days=4)).isoformat(),
        "budget_amount": 3200,
        "currency": "USD",
        "vibes": ["culture", "food"],
        "group_type": "couple",
        "num_travelers": 2,
    }
    body.update(overrides)
    return body


def _prefs(**overrides) -> TripPreferences:
    return TripPreferences(**{**payload(), "group_type": GroupType.COUPLE, **overrides})


def _rec(category: str, rank: int) -> Recommendation:
    return Recommendation(
        id=f"{category}-{rank}",
        name=f"{category.title()} option {rank}",
        category=category,
        description="A well researched option.",
        reasoning="Profile fit.",
        estimated_cost="$40-$80",
        cost_min=40,
        cost_max=80,
        rating=4.7,
        review_count=800,
        location="Kyoto",
        image_search_query=f"kyoto {category}",
        rank=rank,
        score=0.9,
    )


def _events(sse_text: str) -> list[dict]:
    return [json.loads(line[len("data: "):]) for line in sse_text.splitlines() if line.startswith("data: ")]


@pytest.fixture
def api(tmp_path, monkeypatch):
    database_path = tmp_path / "scope.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{database_path}")
    monkeypatch.setattr(db, "DB_PATH", database_path)
    db.dispose_engine()
    db.init_db()
    client = TestClient(main.app)
    registered = client.post(
        "/api/auth/register",
        json={"email": "scope@example.com", "password": "safe-password-1"},
    )
    assert registered.status_code == 200, registered.text
    headers = {"Authorization": f"Bearer {registered.json()['token']}"}
    user_id = db.get_user_by_email("scope@example.com")["user_id"]
    profiles.save_sketch(
        user_id,
        "# Character Sketch\n\nA balanced traveler who likes local culture.\n",
        ["Local culture"],
    )
    yield client, headers, user_id
    db.dispose_engine()


def _stub_research(monkeypatch) -> tuple[list[dict], list[int], set[str]]:
    calls: list[dict] = []
    briefs: list[int] = []
    failing: set[str] = set()

    async def fake_brief(*_args, **_kwargs):
        briefs.append(1)
        return "A saved Kyoto brief."

    async def fake_agents(*_args, agent_names=None, force_refresh=False, **_kwargs):
        names = list(ALL_AGENT_NAMES if agent_names is None else agent_names)
        calls.append({"agent_names": names, "force_refresh": force_refresh})
        completed = failed = 0
        for name in names:
            yield {"event": "agent_started", "agent": name}
            if name in failing:
                failed += 1
                yield {
                    "event": "agent_failed",
                    "agent": name,
                    "error": f"{name} could not finish this search. You can retry safely.",
                    "code": "AGENT_FAILED",
                    "retryable": True,
                }
            else:
                completed += 1
                yield {
                    "event": "agent_completed",
                    "agent": name,
                    "results": [_rec(CATEGORY_OF[name], rank).model_dump() for rank in range(1, 4)],
                }
        yield {"event": "all_complete", "completed": completed, "failed": failed, "status": "complete"}

    monkeypatch.setattr(main, "generate_context_brief", fake_brief)
    monkeypatch.setattr(main, "run_agents_streaming", fake_agents)
    return calls, briefs, failing


def _create(client, headers, **overrides) -> str:
    trip = client.post("/api/trip/preferences", headers=headers, json=payload(**overrides))
    assert trip.status_code == 200, trip.text
    return trip.json()["trip_id"]


def test_scope_defaults_to_the_full_trip():
    assert _prefs().scope == ["transport", "hotel", "activity", "restaurant"]
    assert TripPreferences.model_validate(payload()).scope == list(SCOPE_CATEGORIES)
    assert planning_scope_note(SCOPE_CATEGORIES) is None
    assert planning_scope_note(["restaurant", "hotel", "transport", "activity"]) is None


def test_scope_is_deduplicated_and_canonically_ordered():
    assert _prefs(scope=["restaurant", " Transport ", "restaurant"]).scope == ["transport", "restaurant"]
    assert _prefs(scope=["activity", "hotel"]).scope == ["hotel", "activity"]


@pytest.mark.parametrize(
    "scope, message",
    [(["flights"], "Unknown planning scope"), ([], "Choose at least one thing to plan")],
)
def test_bad_scope_is_a_readable_422(api, scope, message):
    client, headers, _ = api
    response = client.post("/api/trip/preferences", headers=headers, json=payload(scope=scope))

    assert response.status_code == 422
    assert message in response.text
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_agent_categories_and_agents_for_scope_keep_canonical_order():
    assert AGENT_CATEGORIES == CATEGORY_OF
    assert list(AGENT_CATEGORIES) == list(ALL_AGENT_NAMES)
    assert agents_for_scope(["transport"]) == ["Transport Agent"]
    assert agents_for_scope(["restaurant", "transport", "hotel"]) == [
        "Accommodation Agent", "Restaurant Agent", "Transport Agent",
    ]
    assert agents_for_scope(SCOPE_CATEGORIES) == list(ALL_AGENT_NAMES)


def test_research_runs_only_the_scoped_agents(api, monkeypatch):
    client, headers, _ = api
    calls, _briefs, _failing = _stub_research(monkeypatch)
    trip = client.post("/api/trip/preferences", headers=headers, json=payload(scope=["hotel", "transport"]))
    assert trip.status_code == 200, trip.text
    assert trip.json()["preferences"]["scope"] == ["transport", "hotel"]
    trip_id = trip.json()["trip_id"]

    research = client.post(f"/api/trip/{trip_id}/research", headers=headers)
    assert research.status_code == 200
    events = _events(research.text)

    assert calls == [{"agent_names": ["Accommodation Agent", "Transport Agent"], "force_refresh": False}]
    assert events[0]["event"] == "research_started"
    assert events[0]["resumed"] is False
    assert events[0]["agents"] == ["Accommodation Agent", "Transport Agent"]
    assert events[-1]["event"] == "all_complete"
    assert events[-1]["available_categories"] == 2
    state = client.get(f"/api/trip/{trip_id}", headers=headers).json()
    assert state["preferences"]["scope"] == ["transport", "hotel"]
    assert sorted(result["agent_name"] for result in state["research_results"]) == [
        "Accommodation Agent", "Transport Agent",
    ]


def test_partial_retry_and_full_refresh_stay_inside_the_scope(api, monkeypatch):
    client, headers, _ = api
    calls, briefs, failing = _stub_research(monkeypatch)
    trip_id = _create(client, headers, scope=["transport", "hotel"])

    failing.add("Transport Agent")
    assert client.post(f"/api/trip/{trip_id}/research", headers=headers).status_code == 200
    partial = client.get(f"/api/trip/{trip_id}", headers=headers).json()
    assert [result["agent_name"] for result in partial["research_results"]] == ["Accommodation Agent"]
    assert len(partial["research_errors"]) == 1

    failing.clear()
    retried = client.post(f"/api/trip/{trip_id}/research", headers=headers)
    assert retried.status_code == 200
    assert _events(retried.text)[0]["resumed"] is True
    assert calls[1] == {"agent_names": ["Transport Agent"], "force_refresh": False}
    assert len(briefs) == 1
    complete = client.get(f"/api/trip/{trip_id}", headers=headers).json()
    assert complete["research_errors"] == []
    assert sorted(result["agent_name"] for result in complete["research_results"]) == [
        "Accommodation Agent", "Transport Agent",
    ]

    refreshed = client.post(f"/api/trip/{trip_id}/research", headers=headers)
    assert refreshed.status_code == 200
    assert _events(refreshed.text)[0]["resumed"] is False
    assert calls[2] == {"agent_names": ["Accommodation Agent", "Transport Agent"], "force_refresh": True}
    assert len(briefs) == 2


def test_out_of_scope_results_from_an_older_trip_do_not_count(api, monkeypatch):
    client, headers, _ = api
    calls, _briefs, _failing = _stub_research(monkeypatch)
    trip_id = _create(client, headers, scope=["transport"])
    raw = db.load_trip_state(trip_id)
    raw["research_results"] = [
        AgentResult(
            agent_name="Accommodation Agent",
            recommendations=[_rec("hotel", rank) for rank in range(1, 4)],
        ).model_dump(mode="json")
    ]
    raw["research_errors"] = ["Activities Agent could not finish this search. You can retry safely."]
    db.save_trip_state(raw)

    research = client.post(f"/api/trip/{trip_id}/research", headers=headers)
    assert research.status_code == 200
    events = _events(research.text)

    assert events[0]["resumed"] is False
    assert events[0]["agents"] == ["Transport Agent"]
    assert calls == [{"agent_names": ["Transport Agent"], "force_refresh": False}]
    assert events[-1]["available_categories"] == 1


def test_stored_trip_without_scope_loads_as_the_full_trip(tmp_path):
    db.DB_PATH = tmp_path / "legacy.db"
    db.dispose_engine()
    db.init_db()
    now = datetime.now(timezone.utc).isoformat()
    db.create_user("u1", "legacy@example.com", "legacy", "hash", now)
    legacy = {"trip_id": "legacy-trip", "user_id": "u1", "preferences": payload(), "created_at": now}
    assert "scope" not in legacy["preferences"]
    db.save_trip_state(legacy)

    loaded = get_trip("legacy-trip")

    assert loaded is not None
    assert loaded.preferences.scope == list(SCOPE_CATEGORIES)
    assert agents_for_scope(loaded.preferences.scope) == list(ALL_AGENT_NAMES)


def test_feasibility_floor_is_skipped_when_neither_lodging_nor_food_is_planned():
    # conftest raises on any model call, so "unchecked" proves the deterministic
    # floor stepped aside and the (stubbed) model tier was consulted instead.
    def verdict(**overrides) -> str:
        return asyncio.run(feasibility.check_feasibility(_prefs(budget_amount=60, **overrides))).verdict

    assert verdict() == "unrealistic"
    assert verdict(scope=["transport"]) == "unchecked"
    assert verdict(scope=["transport", "activity"]) == "unchecked"
    assert verdict(scope=["transport", "restaurant"]) == "unrealistic"
    assert verdict(scope=["hotel"]) == "unrealistic"


def test_feasibility_prompt_carries_the_scope_note(monkeypatch):
    prompts: list[str] = []

    async def fake(prompt, **_kwargs):
        prompts.append(prompt)
        return json.dumps({"verdict": "ok", "confidence": 0.9})

    monkeypatch.setattr(feasibility, "generate_text", fake)
    asyncio.run(feasibility.check_feasibility(_prefs(scope=["transport", "hotel"])))
    asyncio.run(feasibility.check_feasibility(_prefs()))

    assert "## Planning scope" in prompts[0]
    assert "CheckIn is planning only transport and lodging" in prompts[0]
    assert "must not count against it" in prompts[0]
    assert "Planning scope" not in prompts[1]


def test_fallback_brief_mentions_a_partial_scope():
    partial = build_fallback_context_brief(_prefs(scope=["transport", "hotel"]), "Prefers quiet streets.")

    assert "Mumbai to Kyoto" in partial
    assert "quiet streets" in partial
    assert partial.endswith(
        "Planning scope: CheckIn is planning only transport and lodging; "
        "the traveler arranges the rest separately."
    )
    assert "Planning scope" not in build_fallback_context_brief(_prefs())
    assert planning_scope_note(["restaurant"]) == (
        "Planning scope: CheckIn is planning only dining; the traveler arranges the rest separately."
    )
    assert "transport, lodging and activities" in planning_scope_note(["activity", "transport", "hotel"])


def test_itinerary_prompt_includes_the_scope_note(monkeypatch):
    prompts: list[str] = []
    prefs = _prefs(scope=["activity"])
    prefs = prefs.model_copy(update={"end_date": prefs.start_date})
    selected = [_rec("activity", 1)]
    plan = Itinerary(
        trip_title="One day out",
        trip_summary="Short and focused.",
        days=[DayPlan(day_number=1, date=prefs.start_date.isoformat(), theme="Out and about", items=[
            ItineraryItem(
                time_slot="10:00 AM - 12:00 PM", title="Activity option 1", description="A guided walk.",
                category="activity", cost_estimate="$20", location="Kyoto",
            ),
            ItineraryItem(
                time_slot="Evening", title="Traveler arranges lodging separately", description="Not planned here.",
                category="free_time", cost_estimate="n/a", location="Kyoto",
            ),
        ])],
    )

    async def fake(prompt, **_kwargs):
        prompts.append(prompt)
        return json.dumps(plan.model_dump())

    monkeypatch.setattr(itinerary_module, "generate_text", fake)
    result = asyncio.run(itinerary_module.generate_itinerary(prefs, "brief", selected))

    assert result.days[0].items[1].category == "free_time"
    assert len(prompts) == 1
    assert "## Planning scope" in prompts[0]
    assert "CheckIn is planning only activities" in prompts[0]
    assert "Traveler arranges lodging separately" in prompts[0]
    assert "## Your Task" in prompts[0]

    prompts.clear()
    full = prefs.model_copy(update={"scope": list(SCOPE_CATEGORIES)})
    asyncio.run(itinerary_module.generate_itinerary(full, "brief", selected))
    assert "Planning scope" not in prompts[0]
