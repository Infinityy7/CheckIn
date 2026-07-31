"""Focused transaction/ownership tests for the PostgreSQL-compatible DB facade."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import db
from scripts.migrate_sqlite_to_postgres import load_source


def fresh_db(tmp_path):
    db.dispose_engine()
    db.DB_PATH = tmp_path / "transactions.db"
    db.init_db()


def add_user(user_id: str, email: str) -> None:
    db.create_user(user_id, email, "salt", "hash", datetime.now(timezone.utc).isoformat())


def equal_weights() -> dict:
    return {
        "vibe_weights": {
            "adventure": 0.1,
            "culture": 0.1,
            "food": 0.1,
            "nightlife": 0.1,
            "relaxation": 0.1,
            "nature": 0.1,
            "shopping": 0.1,
            "history": 0.1,
            "romance": 0.1,
            "wellness": 0.1,
        }
    }


def test_user_and_first_session_are_one_transaction(tmp_path):
    fresh_db(tmp_path)
    add_user("u1", "one@example.com")
    expiry = datetime.now(timezone.utc) + timedelta(days=1)
    db.save_session("a" * 64, "u1", expiry)

    with pytest.raises(sqlite3.IntegrityError):
        db.create_user_with_session(
            "u2", "two@example.com", "salt", "hash",
            datetime.now(timezone.utc).isoformat(), "a" * 64, expiry,
        )

    assert db.get_user_by_id("u2") is None


def test_trip_save_and_mutation_preserve_owner(tmp_path):
    fresh_db(tmp_path)
    add_user("u1", "one@example.com")
    add_user("u2", "two@example.com")
    original = {
        "trip_id": "trip-1",
        "user_id": "u1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "preferences": {"destination": "Kyoto"},
        "research_results": [],
    }
    db.save_trip_state(original)

    with pytest.raises(ValueError, match="owner"):
        db.save_trip_state({**original, "user_id": "u2"})
    assert db.load_trip_state("trip-1")["user_id"] == "u1"

    def append_result(state):
        state["research_results"].append({"agent": "activities"})

    updated = db.mutate_trip_state("trip-1", append_result)
    assert updated["research_results"] == [{"agent": "activities"}]

    with pytest.raises(ValueError, match="owner"):
        db.mutate_trip_state("trip-1", lambda state: {**state, "user_id": "u2"})
    assert db.load_trip_state("trip-1")["user_id"] == "u1"


def test_learning_checks_trip_owner_and_persists_locked_adjustments(tmp_path):
    fresh_db(tmp_path)
    add_user("u1", "one@example.com")
    add_user("u2", "two@example.com")
    db.save_profile("u1", "self", "self", "self", "# Character", equal_weights())
    db.save_profile("u2", "self", "self", "self", "# Character", equal_weights())
    db.save_trip_state({
        "trip_id": "trip-1",
        "user_id": "u1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "preferences": {},
    })

    with pytest.raises(ValueError, match="does not belong"):
        db.apply_preference_learning(
            "u2", "trip-1", "selection", "wrong-owner", {"food": 0.02}
        )

    result = db.apply_preference_learning(
        "u1",
        "trip-1",
        "post_trip_rating",
        "rating:trip-1",
        {"food": 0.04},
        rating=5,
        payload={"adjustments": [{"key": "food", "before": 999, "after": 999}]},
    )
    assert result["applied"] is True
    assert result["adjustments"][0]["before"] == 0.1
    assert result["adjustments"][0]["after"] != 999
    assert result["feedback"]["details"]["adjustments"] == result["adjustments"]
    assert result["event"]["payload"]["adjustments"] == result["adjustments"]

    replay = db.apply_preference_learning(
        "u1", "trip-1", "post_trip_rating", "rating:trip-1", {"food": 0.04}, rating=5
    )
    assert replay["applied"] is False
    assert replay["adjustments"] == result["adjustments"]
    assert len(db.list_preference_events("u1")) == 1


def test_postgres_importer_accepts_already_upgraded_sqlite_schema(tmp_path):
    fresh_db(tmp_path)
    user_id = "51fb82b5-9c7d-4a2f-a15c-7f3fb6aab317"
    add_user(user_id, "migrated@example.com")
    db.save_profile(
        user_id,
        "self",
        "self",
        "self",
        "# Character Sketch\n\nA durable traveler.\n",
        equal_weights(),
    )
    db.dispose_engine()

    users, profiles = load_source(db.DB_PATH)
    assert users[0]["user_id"] == user_id
    assert profiles[0]["sketch_md"].startswith("# Character Sketch")
    assert profiles[0]["weights"]["vibe_weights"]["food"] == 0.1
