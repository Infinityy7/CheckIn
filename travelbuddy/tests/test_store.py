"""Trip-store lease and durable mutation behavior."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

import db
from schemas import AgentResult, GroupType, Recommendation, TripPreferences
from store import (
    ResearchAlreadyRunning,
    add_agent_result,
    create_trip,
    finish_research,
    get_trip,
    set_selections,
    start_research,
)


def _trip(tmp_path):
    db.DB_PATH = tmp_path / "store.db"
    db.dispose_engine()
    db.init_db()
    db.create_user("u1", "store@example.com", "aa", "hash", datetime.now(timezone.utc).isoformat())
    return create_trip(TripPreferences(
        destination="Kyoto",
        origin="Mumbai",
        start_date=date(2026, 1, 10),
        end_date=date(2026, 1, 12),
        budget_amount=2000,
        currency="USD",
        vibes=["culture"],
        group_type=GroupType.COUPLE,
        num_travelers=2,
    ), "u1")


def _agent_result(version: str) -> AgentResult:
    return AgentResult(
        agent_name="Activities Agent",
        recommendations=[
            Recommendation(
                id=f"{version}-{rank}",
                name=f"Activity {version}-{rank}",
                category="activity",
                description="A researched cultural activity.",
                reasoning="Matches the saved profile.",
                estimated_cost="$20-$40",
                cost_min=20,
                cost_max=40,
                rating=4.6,
                review_count=700,
                location="Kyoto",
                image_search_query="kyoto activity",
            )
            for rank in range(1, 4)
        ],
    )


def test_live_research_lease_blocks_a_second_worker(tmp_path):
    trip = _trip(tmp_path)
    lease = start_research(trip.trip_id)
    assert lease
    with pytest.raises(ResearchAlreadyRunning):
        start_research(trip.trip_id)
    finish_research(trip.trip_id, lease)
    assert get_trip(trip.trip_id).research_in_progress is False


def test_stale_worker_cannot_release_a_new_lease(tmp_path):
    trip = _trip(tmp_path)
    old_lease = start_research(trip.trip_id)
    stale_time = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    db.mutate_trip_state(
        trip.trip_id,
        lambda state: state.update({"research_started_at": stale_time}),
    )
    new_lease = start_research(trip.trip_id)
    assert new_lease != old_lease

    finish_research(trip.trip_id, old_lease)
    raw = db.load_trip_state(trip.trip_id)
    assert raw["research_in_progress"] is True
    assert raw["research_lease_id"] == new_lease

    finish_research(trip.trip_id, new_lease)
    assert get_trip(trip.trip_id).research_in_progress is False


def test_preserved_results_are_upserted_without_duplicates(tmp_path):
    trip = _trip(tmp_path)
    first_lease = start_research(trip.trip_id)
    add_agent_result(trip.trip_id, _agent_result("old"), first_lease)
    finish_research(trip.trip_id, first_lease)
    set_selections(trip.trip_id, ["old-1"])

    retry_lease = start_research(
        trip.trip_id,
        preserve_results=True,
        preserve_downstream=True,
    )
    during_retry = get_trip(trip.trip_id)
    assert during_retry.research_results[0].recommendations[0].id == "old-1"
    assert during_retry.selections == ["old-1"]

    add_agent_result(trip.trip_id, _agent_result("new"), retry_lease)
    finish_research(trip.trip_id, retry_lease)
    refreshed = get_trip(trip.trip_id)

    assert len(refreshed.research_results) == 1
    assert refreshed.research_results[0].recommendations[0].id == "new-1"


def test_full_refresh_keeps_last_good_results_but_invalidates_selections(tmp_path):
    trip = _trip(tmp_path)
    first_lease = start_research(trip.trip_id)
    add_agent_result(trip.trip_id, _agent_result("old"), first_lease)
    finish_research(trip.trip_id, first_lease)
    set_selections(trip.trip_id, ["old-1"])

    refresh_lease = start_research(trip.trip_id, preserve_results=True)
    refreshed = get_trip(trip.trip_id)

    assert refreshed.research_results[0].recommendations[0].id == "old-1"
    assert refreshed.selections is None
    finish_research(trip.trip_id, refresh_lease)
