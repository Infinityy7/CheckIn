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
        rank=rank,
        score=0.95 - rank * 0.05,
    )


def test_authenticated_profile_research_selection_and_itinerary(tmp_path, monkeypatch):
    db.DB_PATH = tmp_path / "api.db"
    db._conn = None
    db.init_db()
    auth._sessions = {}

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

    async def no_profile_learning(*_args, **_kwargs):
        return None

    monkeypatch.setattr(main, "generate_context_brief", fake_brief)
    monkeypatch.setattr(main, "run_agents_streaming", fake_agents)
    monkeypatch.setattr(main, "generate_itinerary", fake_itinerary_builder)
    monkeypatch.setattr(profiles, "update_sketch_from_trip", no_profile_learning)

    client = TestClient(main.app)
    auth_response = client.post("/api/auth/register", json={"email": "flow@example.com", "password": "safe-password-1"})
    assert auth_response.status_code == 200
    token = auth_response.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    user_id = auth._sessions[token]

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
    trip_id = trip.json()["trip_id"]
    research = client.post(f"/api/trip/{trip_id}/research", headers=headers)
    assert research.status_code == 200
    assert '"event": "all_complete"' in research.text

    state = client.get(f"/api/trip/{trip_id}", headers=headers).json()
    assert len(state["research_results"]) == 4
    selected_ids = [result["recommendations"][0]["id"] for result in state["research_results"]]
    selected = client.post(f"/api/trip/{trip_id}/select", headers=headers, json={"selections": selected_ids})
    assert selected.json()["count"] == 4

    generated = client.post(f"/api/trip/{trip_id}/itinerary", headers=headers)
    assert generated.status_code == 200
    assert '"event": "itinerary_complete"' in generated.text
    saved = client.get(f"/api/trip/{trip_id}", headers=headers).json()
    assert saved["itinerary"]["trip_title"] == "Kyoto Between Lanterns"

    reset = client.post("/api/profile/character/reset", headers=headers)
    assert reset.json()["intake_complete"] is False
    assert client.get("/api/auth/me", headers=headers).json()["intake_complete"] is False
