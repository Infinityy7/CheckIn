"""Black-box API coverage for the persistent personalization loop."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import auth
import db
import main
import profiles
from schemas import DayPlan, Itinerary, ItineraryItem


QUESTION_ANSWERS = {
    "spontaneity": 0.8,
    "top_vibes": ["nature", "relaxation", "romance"],
    "spend_preferences": {"splurge": "experiences", "save": "shopping"},
    "chronotype": "late",
    "archetype": "slow_traveler",
    "default_party": "partner",
    "food_adventurousness": 0.7,
    "constraints": ["theme_parks", "vegetarian"],
    "perfect_moment": "A quiet sunrise followed by breakfast.",
}


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    database_path = tmp_path / "personalization-api.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{database_path}")
    db.DB_PATH = database_path
    db.dispose_engine()
    db.init_db()
    with TestClient(main.app) as client:
        yield client
    db.dispose_engine()


def register(client: TestClient, email: str = "traveler@example.com") -> tuple[dict[str, str], str]:
    response = client.post(
        "/api/auth/register",
        json={"email": email, "password": "safe-password-1"},
    )
    assert response.status_code == 200
    token = response.json()["token"]
    return {"Authorization": f"Bearer {token}"}, token


def answer_questions(client: TestClient, headers: dict[str, str], answers=None) -> None:
    for question_id, value in (answers or QUESTION_ANSWERS).items():
        response = client.put(
            f"/api/profile/intake/answers/{question_id}",
            headers=headers,
            json={"value": value},
        )
        assert response.status_code == 200, response.text


def complete_profile(client: TestClient, headers: dict[str, str], monkeypatch) -> dict:
    calls = {"count": 0}

    async def deterministic_polish(*_args, **_kwargs):
        calls["count"] += 1
        return "A calm, curious traveler drawn to nature, local moments, and unhurried discovery."

    monkeypatch.setattr(profiles, "generate_text", deterministic_polish)
    answer_questions(client, headers)
    response = client.post("/api/profile/intake/complete", headers=headers)
    assert response.status_code == 200, response.text
    assert calls["count"] == 1
    return response.json()


def test_nine_question_answers_persist_and_resume(api_client, monkeypatch):
    headers, _token = register(api_client, "resume@example.com")

    initial = api_client.get("/api/profile/intake", headers=headers)
    assert initial.status_code == 200
    assert initial.json() == {
        "questionnaireVersion": "personalisation-v1",
        "status": "not_started",
        "currentIndex": 0,
        "total": 9,
        "answers": {},
        "currentQuestion": initial.json()["currentQuestion"],
        "profile": None,
    }
    assert initial.json()["currentQuestion"]["id"] == "spontaneity"

    first_four = dict(list(QUESTION_ANSWERS.items())[:4])
    answer_questions(api_client, headers, first_four)

    # Simulate a process reconnect: the bearer session and questionnaire draft
    # must both be database-backed rather than tied to an in-memory chat.
    db.dispose_engine()
    with TestClient(main.app) as resumed_client:
        resumed = resumed_client.get("/api/profile/intake", headers=headers)
        assert resumed.status_code == 200
        body = resumed.json()
        assert body["currentIndex"] == 4
        assert body["currentQuestion"]["id"] == "archetype"
        assert body["answers"] == first_four

        answer_questions(
            resumed_client,
            headers,
            dict(list(QUESTION_ANSWERS.items())[4:]),
        )
        ready = resumed_client.get("/api/profile/intake", headers=headers).json()
        assert ready["status"] == "ready_to_complete"
        assert ready["currentIndex"] == ready["total"] == 9
        assert ready["currentQuestion"] is None
        assert ready["answers"] == QUESTION_ANSWERS


def test_completion_returning_user_and_structured_version_conflict(api_client, monkeypatch):
    headers, _token = register(api_client, "returning@example.com")
    profile = complete_profile(api_client, headers, monkeypatch)

    assert profile["characterMd"].startswith("# Character Sketch")
    assert profile["summary"].startswith("A calm, curious traveler")
    assert profile["weights"]["schemaVersion"] == 1
    assert profile["weights"]["spontaneity"] == 0.8
    assert profile["weights"]["dealBreakers"] == ["theme_parks"]
    assert profile["weights"]["dietaryRequirements"] == ["vegetarian"]
    assert profile["rawAnswers"] == QUESTION_ANSWERS

    me = api_client.get("/api/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["intake_complete"] is True

    # A returning user gets the same durable artifacts through a fresh login.
    assert api_client.post("/api/auth/logout", headers=headers).status_code == 200
    login = api_client.post(
        "/api/auth/login",
        json={"email": "returning@example.com", "password": "safe-password-1"},
    )
    assert login.status_code == 200
    returning_headers = {"Authorization": f"Bearer {login.json()['token']}"}
    restored = api_client.get("/api/profile/character", headers=returning_headers).json()
    assert restored["version"] == profile["version"]
    assert restored["characterMd"] == profile["characterMd"]
    assert restored["weights"] == profile["weights"]

    edited_weights = {**restored["weights"]}
    edited_weights["foodAdventurousness"] = 0.9
    edited = api_client.put(
        "/api/profile/character",
        headers=returning_headers,
        json={
            "summary": "A calm traveler who now wants broader local food discoveries on every trip.",
            "weights": edited_weights,
            "expectedVersion": restored["version"],
        },
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["version"] == restored["version"] + 1
    assert edited.json()["weights"]["foodAdventurousness"] == 0.9
    assert edited.json()["summary"].startswith("A calm traveler who now wants")

    stale = api_client.put(
        "/api/profile/character",
        headers=returning_headers,
        json={
            "summary": "This stale browser tab must not overwrite the newer profile version.",
            "weights": restored["weights"],
            "expectedVersion": restored["version"],
        },
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "CONFLICT"
    assert api_client.get("/api/profile/character", headers=returning_headers).json()["version"] == edited.json()["version"]


def test_selection_and_past_trip_rating_learning_are_idempotent(api_client, monkeypatch):
    headers, _token = register(api_client, "learning@example.com")
    initial_profile = complete_profile(api_client, headers, monkeypatch)

    async def deterministic_brief(*_args, **_kwargs):
        return "A compact Kyoto plan with local food and nature."

    recommendations = [
        {
            "id": "activity-food-1",
            "name": "Forest breakfast walk",
            "category": "activity",
            "description": "A verified morning walk and local breakfast.",
            "reasoning": "Matches nature and food preferences.",
            "estimated_cost": "$30",
            "cost_min": 30,
            "cost_max": 30,
            "rating": 4.8,
            "review_count": 420,
            "location": "Northern Kyoto",
            "image_search_query": "Kyoto forest breakfast",
            "vibe_tags": ["food", "nature"],
            "constraint_tags": [],
            "dietary_tags": ["vegetarian"],
        },
        {
            "id": "activity-nightlife-2",
            "name": "Late club crawl",
            "category": "activity",
            "description": "A verified late-night option.",
            "reasoning": "An alternative the traveler did not choose.",
            "estimated_cost": "$45",
            "cost_min": 45,
            "cost_max": 45,
            "rating": 4.6,
            "review_count": 300,
            "location": "Central Kyoto",
            "image_search_query": "Kyoto nightlife",
            "vibe_tags": ["nightlife"],
            "constraint_tags": [],
            "dietary_tags": [],
        },
        {
            "id": "activity-culture-3",
            "name": "Temple craft hour",
            "category": "activity",
            "description": "A verified small-group cultural workshop.",
            "reasoning": "A grounded cultural alternative.",
            "estimated_cost": "$35",
            "cost_min": 35,
            "cost_max": 35,
            "rating": 4.5,
            "review_count": 180,
            "location": "Eastern Kyoto",
            "image_search_query": "Kyoto temple craft",
            "vibe_tags": ["culture"],
            "constraint_tags": [],
            "dietary_tags": [],
        },
    ]

    async def deterministic_agents(*_args, **_kwargs):
        yield {"event": "agent_started", "agent": "Activities Agent"}
        yield {
            "event": "agent_completed",
            "agent": "Activities Agent",
            "results": recommendations,
        }
        yield {"event": "all_complete", "completed": 1, "failed": 0}

    itinerary = Itinerary(
        trip_title="A January Kyoto Reset",
        trip_summary="A short past trip used to verify the feedback loop.",
        days=[DayPlan(
            day_number=1,
            date="2026-01-10",
            theme="Forest and food",
            items=[ItineraryItem(
                time_slot="10:00 AM - 12:00 PM",
                title="Forest breakfast walk",
                description="Walk first, then a vegetarian breakfast.",
                category="activity",
                cost_estimate="$30",
                location="Northern Kyoto",
            )],
        )],
    )

    async def deterministic_itinerary(*_args, **_kwargs):
        return itinerary

    monkeypatch.setattr(main, "generate_context_brief", deterministic_brief)
    monkeypatch.setattr(main, "run_agents_streaming", deterministic_agents)
    monkeypatch.setattr(main, "generate_itinerary", deterministic_itinerary)

    trip = api_client.post(
        "/api/trip/preferences",
        headers=headers,
        json={
            "destination": "Kyoto",
            "origin": "Mumbai",
            "start_date": "2026-10-12",
            "end_date": "2026-10-13",
            "budget_amount": 2200,
            "currency": "USD",
            "vibes": ["food", "nature"],
            "group_type": "couple",
            "num_travelers": 2,
            "cotravellers": [],
        },
    )
    assert trip.status_code == 200, trip.text
    trip_id = trip.json()["trip_id"]
    # New trips must start in the future; the post-trip check-in below needs a
    # finished trip, so move the stored dates back to match the itinerary fixture.
    db.mutate_trip_state(
        trip_id,
        lambda state: state["preferences"].update({"start_date": "2026-01-10", "end_date": "2026-01-11"}),
    )

    research = api_client.post(f"/api/trip/{trip_id}/research", headers=headers)
    assert research.status_code == 200
    assert '"event": "all_complete"' in research.text
    selected = api_client.post(
        f"/api/trip/{trip_id}/select",
        headers=headers,
        json={"selections": ["activity-food-1"]},
    )
    assert selected.status_code == 200

    generated = api_client.post(f"/api/trip/{trip_id}/itinerary", headers=headers)
    assert generated.status_code == 200
    assert '"event": "itinerary_complete"' in generated.text
    before_check_in = api_client.get("/api/profile/character", headers=headers).json()
    assert before_check_in["version"] == initial_profile["version"]

    # Building or rebuilding an itinerary is not post-trip evidence. Final
    # choices and the rating are learned together only after the trip.
    assert api_client.post(f"/api/trip/{trip_id}/itinerary", headers=headers).status_code == 200
    after_rebuild = api_client.get("/api/profile/character", headers=headers).json()
    assert after_rebuild == before_check_in

    pending = api_client.get("/api/trips/pending-check-in", headers=headers)
    assert pending.status_code == 200
    assert pending.json()["trip"]["trip_id"] == trip_id

    rating = api_client.put(
        f"/api/trip/{trip_id}/post-trip-feedback",
        headers=headers,
        json={"overall_rating": 5},
    )
    assert rating.status_code == 200, rating.text
    rated_once = rating.json()
    assert rated_once["postTrip"]["eligible"] is True
    assert rated_once["postTrip"]["rating"] == 5
    assert rated_once["profile"]["version"] == initial_profile["version"] + 1
    assert rated_once["profile"]["weights"]["vibeWeights"]["food"] > initial_profile["weights"]["vibeWeights"]["food"]
    assert rated_once["profile"]["weights"]["vibeWeights"]["nightlife"] <= initial_profile["weights"]["vibeWeights"]["nightlife"]
    assert rated_once["postTrip"]["adjustments"]

    retry = api_client.put(
        f"/api/trip/{trip_id}/post-trip-feedback",
        headers=headers,
        json={"overall_rating": 5},
    )
    assert retry.status_code == 200
    assert retry.json()["profile"]["version"] == rated_once["profile"]["version"]
    assert retry.json()["profile"]["weights"] == rated_once["profile"]["weights"]
    assert retry.json()["postTrip"] == rated_once["postTrip"]

    saved_trip = api_client.get(f"/api/trip/{trip_id}", headers=headers).json()
    assert saved_trip["postTrip"]["rating"] == 5
    assert api_client.get("/api/trips/pending-check-in", headers=headers).json() == {"trip": None}
