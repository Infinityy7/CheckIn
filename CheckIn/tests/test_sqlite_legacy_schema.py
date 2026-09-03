"""A pre-patch SQLite file keeps working after the trips table learns idempotency."""

from __future__ import annotations

import auth
import db
from schemas import TripPreferences
from store import create_trip, get_trip_by_idempotency_key


def test_legacy_sqlite_trips_table_gains_the_idempotency_column_and_index(tmp_path):
    db.DB_PATH = tmp_path / "legacy.db"
    db.dispose_engine()
    db.init_db()
    with db._connect().begin() as connection:
        connection.exec_driver_sql("DROP INDEX IF EXISTS uq_trips_user_idempotency")
        connection.exec_driver_sql("ALTER TABLE trips DROP COLUMN idempotency_key")
    db.dispose_engine()

    db.init_db()

    with db._connect().connect() as connection:
        columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(trips)")}
        indexes = {row[1] for row in connection.exec_driver_sql("PRAGMA index_list(trips)")}
    assert "idempotency_key" in columns
    assert "uq_trips_user_idempotency" in indexes

    auth.register("legacy@example.com", "safe-password-1")
    user_id = db.get_user_by_email("legacy@example.com")["user_id"]
    prefs = TripPreferences(
        destination="Kyoto", origin="Mumbai", start_date="2026-10-12", end_date="2026-10-14",
        budget_amount=1800, currency="USD", vibes=["culture"], group_type="couple", num_travelers=2,
    )
    created = create_trip(prefs, user_id=user_id, idempotency_key="legacy-key-0001")
    assert get_trip_by_idempotency_key(user_id, "legacy-key-0001").trip_id == created.trip_id
    assert db.list_trip_states(user_id)[0]["trip_id"] == created.trip_id
