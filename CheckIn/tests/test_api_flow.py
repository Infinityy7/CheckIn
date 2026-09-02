"""End-to-end API wiring without external LLM calls."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

import auth
import db
import main
import profiles
from schemas import DayPlan, Itinerary, ItineraryItem, Recommendation


def recommendation(category: str, rank: int) -> Recommendation:
    return Recommendation(
        id=f"{category}-{rank}",
        name=f"{category.title()} option {rank}",
        category=category,
        description="A well researched option with a strong sense of place.",
        reasoning="Matches the saved character profile.",
        estimated_cost="$40-$80",
        cost_min=40,
        cost_max=80,
        rating=4.7,
        review_count=800,
        location="Central Kyoto",
        image_search_query=f"kyoto {category}",
        vibe_tags=["culture"],
        rank=rank,
        score=0.95 - rank * 0.05,
    )


def test_authenticated_profile_research_selection_and_itinerary(tmp_path, monkeypatch):
    db.DB_PATH = tmp_path / "api.db"
    db._conn = None
    db.init_db()

    async def fake_brief(*_args, **_kwargs):
        return "A balanced Kyoto trip shaped by local food and unhurried exploration."

    async def fake_agents(*_args, **_kwargs):
        for name, category in [
            ("Accommodation Agent", "hotel"),
            ("Activities Agent", "activity"),
            ("Restaurant Agent", "restaurant"),
            ("Transport Agent", "transport"),
        ]:
            yield {"event": "agent_started", "agent": name}
            yield {
                "event": "agent_completed",
                "agent": name,
                "results": [recommendation(category, rank).model_dump() for rank in range(1, 4)],
            }
        yield {"event": "all_complete"}

    fake_itinerary = Itinerary(
        trip_title="Kyoto Between Lanterns",
        trip_summary="A balanced route with room for discovery.",
        days=[DayPlan(
            day_number=1,
            date="2026-10-12",
            theme="Gion at first light",
            items=[ItineraryItem(
                time_slot="8:00 AM - 10:00 AM",
                title="Fushimi dawn walk",
                description="Arrive before the crowds.",
                category="activity",
                cost_estimate="$10",
                location="Fushimi",
            )],
        )],
    )

    async def fake_itinerary_builder(*_args, **_kwargs):
        return fake_itinerary

    monkeypatch.setattr(main, "generate_context_brief", fake_brief)
    monkeypatch.setattr(main, "run_agents_streaming", fake_agents)
    monkeypatch.setattr(main, "generate_itinerary", fake_itinerary_builder)

    client = TestClient(main.app)
    auth_response = client.post("/api/auth/register", json={"email": "flow@example.com", "password": "safe-password-1"})
    assert auth_response.status_code == 200
    token = auth_response.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    user_id = db.get_user_by_email("flow@example.com")["user_id"]

    profiles.save_sketch(
        user_id,
        """# Character Sketch
keywords: local food, temples

```json
{"likes":{"local food":3},"dislikes":{},"diet":[],"pace":"moderate","traits":{"pace":"balanced","localVsTourist":0.8}}
```

A balanced traveler who values local food and quieter cultural experiences.
""",
        ["Balanced pace", "Local food", "Quiet culture"],
    )

    me = client.get("/api/auth/me", headers=headers)
    assert me.json()["intake_complete"] is True
    character = client.get("/api/profile/character", headers=headers)
    assert character.status_code == 200
    assert character.json()["traits"]["localVsTourist"] == 0.8

    edited = client.put(
        "/api/profile/character",
        headers=headers,
        json={"summary": "A balanced traveler who now wants more spontaneous local discoveries.", "traits": {**character.json()["traits"], "spontaneity": 0.9}},
    )
    assert edited.status_code == 200
    assert edited.json()["traits"]["spontaneity"] == 0.9

    trip = client.post("/api/trip/preferences", headers=headers, json={
        "destination": "Kyoto", "origin": "Mumbai", "start_date": "2026-10-12", "end_date": "2026-10-18",
        "budget_amount": 3200, "currency": "USD", "vibes": ["culture", "food", "nature"],
        "group_type": "couple", "num_travelers": 2, "cotravellers": [],
    })
    assert trip.status_code == 200, trip.text
    trip_id = trip.json()["trip_id"]
    assert trip.json()["status"] == "received"
    assert trip.json()["replayed"] is False
    # LLM calls are stubbed out in unit tests, so the advisory check fails open
    assert trip.json()["feasibility"]["verdict"] == "unchecked"
    research = client.post(f"/api/trip/{trip_id}/research", headers=headers)
    assert research.status_code == 200
    assert '"event": "all_complete"' in research.text

    state = client.get(f"/api/trip/{trip_id}", headers=headers).json()
    assert len(state["research_results"]) == 4
    feedback_item = state["research_results"][1]["recommendations"][0]
    feedback = client.post(
        "/api/profile/character/feedback",
        headers=headers,
        json={
            "trip_id": trip_id,
            "recommendation_id": feedback_item["id"],
            "sentiment": "dislike",
        },
    )
    assert feedback.status_code == 200
    assert db.list_preference_events(user_id)[0]["event_type"] == "explicit_dislike"

    selected_ids = [result["recommendations"][0]["id"] for result in state["research_results"]]
    selected = client.post(f"/api/trip/{trip_id}/select", headers=headers, json={"selections": selected_ids})
    assert selected.json()["count"] == 4

    # A refreshed shortlist replaces IDs and must invalidate old selections.
    refreshed = client.post(f"/api/trip/{trip_id}/research", headers=headers)
    assert refreshed.status_code == 200
    assert client.get(f"/api/trip/{trip_id}", headers=headers).json()["selections"] is None
    client.post(f"/api/trip/{trip_id}/select", headers=headers, json={"selections": selected_ids})

    generated = client.post(f"/api/trip/{trip_id}/itinerary", headers=headers)
    assert generated.status_code == 200
    assert '"event": "itinerary_complete"' in generated.text
    saved = client.get(f"/api/trip/{trip_id}", headers=headers).json()
    assert saved["itinerary"]["trip_title"] == "Kyoto Between Lanterns"

    async def never_called_builder(*_args, **_kwargs):
        raise AssertionError("an unchanged selection set must replay the stored itinerary")

    monkeypatch.setattr(main, "generate_itinerary", never_called_builder)
    replayed = client.post(f"/api/trip/{trip_id}/itinerary", headers=headers)
    assert replayed.status_code == 200
    assert '"event": "itinerary_complete"' in replayed.text
    assert '"replayed": true' in replayed.text

    async def failed_itinerary_builder(*_args, **_kwargs):
        raise RuntimeError("provider-secret-that-must-not-reach-the-browser")

    monkeypatch.setattr(main, "generate_itinerary", failed_itinerary_builder)
    # Unchanged selections replay the stored itinerary without a model call, so
    # a changed selection set is what forces the real (failing) rebuild below.
    client.post(f"/api/trip/{trip_id}/select", headers=headers, json={"selections": selected_ids[:3]})
    assert client.get(f"/api/trip/{trip_id}", headers=headers).json()["itinerary"] is None
    failed_generation = client.post(
        f"/api/trip/{trip_id}/itinerary",
        headers={**headers, "X-Request-ID": "itinerary-test-request"},
    )
    assert '"code": "ITINERARY_FAILED"' in failed_generation.text
    assert "provider-secret" not in failed_generation.text
    assert '"request_id": "itinerary-test-request"' in failed_generation.text

    reset = client.post("/api/profile/character/reset", headers=headers)
    assert reset.json()["intake_complete"] is False
    assert client.get("/api/auth/me", headers=headers).json()["intake_complete"] is False


def test_json_errors_have_a_stable_shape_and_request_id():
    client = TestClient(main.app)
    response = client.get(
        "/api/auth/me",
        headers={"X-Request-ID": "support-case-123"},
    )

    assert response.status_code == 401
    assert response.headers["X-Request-ID"] == "support-case-123"
    assert response.json() == {
        "detail": "Not logged in",
        "error": {
            "code": "UNAUTHORIZED",
            "message": "Not logged in",
            "request_id": "support-case-123",
            "retryable": False,
        },
    }


def test_validation_errors_are_readable():
    client = TestClient(main.app)
    response = client.post(
        "/api/auth/register",
        json={"email": "not-an-email", "password": "short"},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["request_id"]
    assert body["error"]["details"]
    assert isinstance(body["detail"], str)


def test_detailed_agent_health_requires_authentication():
    response = TestClient(main.app).get("/api/health/agents")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_partial_research_retry_runs_only_missing_category(tmp_path, monkeypatch):
    db.DB_PATH = tmp_path / "partial-retry.db"
    db.dispose_engine()
    db.init_db()

    context_calls = 0
    agent_calls: list[list[str]] = []
    categories = {
        "Accommodation Agent": "hotel",
        "Activities Agent": "activity",
        "Restaurant Agent": "restaurant",
        "Transport Agent": "transport",
    }

    async def fake_brief(*_args, **_kwargs):
        nonlocal context_calls
        context_calls += 1
        return "A saved, reusable Kyoto context brief."

    async def flaky_agents(*_args, agent_names=None, **_kwargs):
        names = list(agent_names or categories)
        agent_calls.append(names)
        should_fail_restaurant = len(agent_calls) in {1, 3}
        completed = 0
        failed = 0
        for name in names:
            yield {"event": "agent_started", "agent": name}
            if should_fail_restaurant and name == "Restaurant Agent":
                failed += 1
                yield {
                    "event": "agent_failed",
                    "agent": name,
                    "error": "Restaurant Agent could not finish this search. You can retry safely.",
                    "code": "AGENT_FAILED",
                    "retryable": True,
                }
            else:
                completed += 1
                yield {
                    "event": "agent_completed",
                    "agent": name,
                    "results": [
                        recommendation(categories[name], rank).model_dump()
                        for rank in range(1, 4)
                    ],
                }
        yield {
            "event": "all_complete",
            "completed": completed,
            "failed": failed,
            "status": "partial" if failed and completed else "complete",
        }

    monkeypatch.setattr(main, "generate_context_brief", fake_brief)
    monkeypatch.setattr(main, "run_agents_streaming", flaky_agents)

    client = TestClient(main.app, raise_server_exceptions=False)
    registered = client.post(
        "/api/auth/register",
        json={"email": "partial@example.com", "password": "safe-password-1"},
    )
    headers = {"Authorization": f"Bearer {registered.json()['token']}"}
    user_id = db.get_user_by_email("partial@example.com")["user_id"]
    profiles.save_sketch(
        user_id,
        "# Character Sketch\n\nA balanced traveler who likes local culture.\n",
        ["Local culture"],
    )
    trip = client.post(
        "/api/trip/preferences",
        headers=headers,
        json={
            "destination": "Kyoto",
            "origin": "Mumbai",
            "start_date": "2026-10-12",
            "end_date": "2026-10-18",
            "budget_amount": 3200,
            "currency": "USD",
            "vibes": ["culture", "food"],
            "group_type": "couple",
            "num_travelers": 2,
            "cotravellers": [],
        },
    )
    assert trip.status_code == 200, trip.text
    assert trip.json()["status"] == "received"
    trip_id = trip.json()["trip_id"]

    first = client.post(f"/api/trip/{trip_id}/research", headers=headers)
    assert first.status_code == 200
    partial = client.get(f"/api/trip/{trip_id}", headers=headers).json()
    assert len(partial["research_results"]) == 3
    assert len(partial["research_errors"]) == 1

    retried = client.post(f"/api/trip/{trip_id}/research", headers=headers)
    assert retried.status_code == 200
    complete = client.get(f"/api/trip/{trip_id}", headers=headers).json()

    assert agent_calls[1] == ["Restaurant Agent"]
    assert context_calls == 1
    assert len(complete["research_results"]) == 4
    assert len({result["agent_name"] for result in complete["research_results"]}) == 4
    assert complete["research_errors"] == []

    # A later full refresh keeps the old Restaurant cards if that agent fails,
    # but the following retry must still target only that failed category.
    failed_refresh = client.post(f"/api/trip/{trip_id}/research", headers=headers)
    assert failed_refresh.status_code == 200
    stale = client.get(f"/api/trip/{trip_id}", headers=headers).json()
    assert len(stale["research_results"]) == 4
    assert len(stale["research_errors"]) == 1

    retry_failed_refresh = client.post(f"/api/trip/{trip_id}/research", headers=headers)
    assert retry_failed_refresh.status_code == 200
    assert agent_calls[2] == list(categories)
    assert agent_calls[3] == ["Restaurant Agent"]
    assert context_calls == 2

    def broken_profile(_user_id):
        raise RuntimeError("profile storage unavailable")

    monkeypatch.setattr(profiles, "load_sketch", broken_profile)
    failed_before_stream = client.post(f"/api/trip/{trip_id}/research", headers=headers)
    assert failed_before_stream.status_code == 500
    assert client.get(f"/api/trip/{trip_id}", headers=headers).json()["research_in_progress"] is False
