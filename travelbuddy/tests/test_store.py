"""Trip-store lease and durable mutation behavior."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

import db
from schemas import GroupType, TripPreferences
from store import ResearchAlreadyRunning, create_trip, finish_research, get_trip, start_research


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
