"""Trip creation: bounded feasibility, idempotent replay, held verdicts, config guards."""

from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import db
import feasibility
import main
import profiles
from schemas import (
    ChatInput,
    FeasibilityReport,
    GroupType,
    Recommendation,
    SuggestedChanges,
    TripPreferences,
)
from store import create_trip

KEY = "11111111-2222-3333-4444-555555555555"
OTHER_KEY = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _today() -> date:
    return datetime.now(timezone.utc).date()


def payload(**overrides) -> dict:
    start = _today() + timedelta(days=60)
    body = {
        "destination": "Kyoto",
        "origin": "Mumbai",
        "start_date": start.isoformat(),
        "end_date": (start + timedelta(days=6)).isoformat(),
        "budget_amount": 3200,
        "currency": "USD",
        "vibes": ["culture", "food"],
        "group_type": "couple",
        "num_travelers": 2,
        "cotravellers": [],
    }
    body.update(overrides)
    return body


def _prefs(**overrides) -> TripPreferences:
    return TripPreferences(**{**payload(), "group_type": GroupType.COUPLE, **overrides})


def stub_feasibility(monkeypatch, verdict: str) -> list[TripPreferences]:
    calls: list[TripPreferences] = []

    async def fake(prefs: TripPreferences) -> FeasibilityReport:
        calls.append(prefs)
        if verdict == "unrealistic":
            return FeasibilityReport(
                verdict="unrealistic",
                confidence=0.95,
                reason="That budget cannot cover lodging and food.",
                suggestion_text="Try at least 6000 USD.",
                suggested_changes=SuggestedChanges(budget_amount=6000),
            )
        return FeasibilityReport(verdict=verdict, confidence=0.9)

    monkeypatch.setattr(main, "check_feasibility", fake)
    return calls


def trips_for(user_id: str) -> list[dict]:
    return db.list_trip_states(user_id)


@pytest.fixture
def api(tmp_path, monkeypatch):
    database_path = tmp_path / "trip-create.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{database_path}")
    monkeypatch.setattr(db, "DB_PATH", database_path)
    db.dispose_engine()
    db.init_db()
    client = TestClient(main.app)
    registered = client.post(
        "/api/auth/register",
        json={"email": "creator@example.com", "password": "safe-password-1"},
    )
    assert registered.status_code == 200, registered.text
    headers = {"Authorization": f"Bearer {registered.json()['token']}"}
    user_id = db.get_user_by_email("creator@example.com")["user_id"]
    profiles.save_sketch(
        user_id,
        "# Character Sketch\n\nA balanced traveler who likes local culture.\n",
        ["Local culture"],
    )
    yield client, headers, user_id
    db.dispose_engine()


def test_same_idempotency_key_replays_the_trip_without_a_second_feasibility_call(api, monkeypatch):
    client, headers, user_id = api
    calls = stub_feasibility(monkeypatch, "tight")
    key_headers = {**headers, "Idempotency-Key": KEY}

    first = client.post("/api/trip/preferences", headers=key_headers, json=payload())
    second = client.post("/api/trip/preferences", headers=key_headers, json=payload())

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["status"] == "received"
    assert first.json()["replayed"] is False
    assert second.json()["status"] == "received"
    assert second.json()["replayed"] is True
    assert second.json()["trip_id"] == first.json()["trip_id"]
    assert second.json()["feasibility"] == first.json()["feasibility"]
    assert second.json()["feasibility"]["verdict"] == "tight"
    assert len(calls) == 1
    assert len(trips_for(user_id)) == 1

    stored = client.get(f"/api/trip/{first.json()['trip_id']}", headers=headers).json()
    assert stored["feasibility"]["verdict"] == "tight"

    fresh = client.post(
        "/api/trip/preferences", headers={**headers, "Idempotency-Key": OTHER_KEY}, json=payload()
    )
    assert fresh.json()["replayed"] is False
    assert fresh.json()["trip_id"] != first.json()["trip_id"]
    assert len(trips_for(user_id)) == 2


def test_missing_idempotency_key_keeps_legacy_create_per_request(api, monkeypatch):
    client, headers, user_id = api
    stub_feasibility(monkeypatch, "ok")

    first = client.post("/api/trip/preferences", headers=headers, json=payload())
    second = client.post("/api/trip/preferences", headers=headers, json=payload())

    assert first.json()["trip_id"] != second.json()["trip_id"]
    assert first.json()["replayed"] is False and second.json()["replayed"] is False
    assert len(trips_for(user_id)) == 2


def test_unrealistic_verdict_holds_until_acknowledged_then_creates_one_trip(api, monkeypatch):
    client, headers, user_id = api
    calls = stub_feasibility(monkeypatch, "unrealistic")
    key_headers = {**headers, "Idempotency-Key": KEY}

    held = client.post("/api/trip/preferences", headers=key_headers, json=payload())
    assert held.status_code == 200, held.text
    assert held.json() == {
        "trip_id": None,
        "status": "held",
        "replayed": False,
        "preferences": held.json()["preferences"],
        "feasibility": held.json()["feasibility"],
    }
    assert held.json()["feasibility"]["verdict"] == "unrealistic"
    assert held.json()["feasibility"]["suggested_changes"]["budget_amount"] == 6000
    assert held.json()["preferences"]["destination"] == "Kyoto"
    assert trips_for(user_id) == []

    acknowledged = client.post(
        "/api/trip/preferences",
        headers=key_headers,
        json=payload(feasibility_acknowledged=True),
    )
    assert acknowledged.status_code == 200, acknowledged.text
    assert acknowledged.json()["status"] == "received"
    assert acknowledged.json()["replayed"] is False
    assert acknowledged.json()["trip_id"]
    assert len(calls) == 1
    assert len(trips_for(user_id)) == 1

    retried = client.post(
        "/api/trip/preferences",
        headers=key_headers,
        json=payload(feasibility_acknowledged=True),
    )
    assert retried.json()["replayed"] is True
    assert retried.json()["trip_id"] == acknowledged.json()["trip_id"]
    assert len(trips_for(user_id)) == 1


def test_applying_a_suggestion_after_a_hold_leaves_no_orphan_trip(api, monkeypatch):
    client, headers, user_id = api
    stub_feasibility(monkeypatch, "unrealistic")

    held = client.post(
        "/api/trip/preferences", headers={**headers, "Idempotency-Key": KEY}, json=payload()
    )
    assert held.json()["status"] == "held"
    assert trips_for(user_id) == []

    stub_feasibility(monkeypatch, "ok")
    revised = client.post(
        "/api/trip/preferences",
        headers={**headers, "Idempotency-Key": OTHER_KEY},
        json=payload(budget_amount=6000),
    )
    assert revised.status_code == 200, revised.text
    assert revised.json()["status"] == "received"

    trips = trips_for(user_id)
    assert len(trips) == 1
    assert trips[0]["trip_id"] == revised.json()["trip_id"]
    assert trips[0]["preferences"]["budget_amount"] == 6000


@pytest.mark.parametrize("bad_key", ["short", "has space in it", "x" * 129, "bad/slash-key"])
def test_malformed_idempotency_key_is_a_400(api, monkeypatch, bad_key):
    client, headers, user_id = api
    calls = stub_feasibility(monkeypatch, "ok")

    response = client.post(
        "/api/trip/preferences", headers={**headers, "Idempotency-Key": bad_key}, json=payload()
    )

    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "BAD_REQUEST"
    assert "Idempotency-Key" in response.json()["detail"]
    assert calls == []
    assert trips_for(user_id) == []


def test_past_start_date_is_rejected_but_today_is_allowed(api, monkeypatch):
    client, headers, user_id = api
    stub_feasibility(monkeypatch, "ok")
    yesterday = _today() - timedelta(days=1)

    past = client.post(
        "/api/trip/preferences",
        headers=headers,
        json=payload(start_date=yesterday.isoformat(), end_date=_today().isoformat()),
    )
    assert past.status_code == 422, past.text
    assert past.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "in the past" in json.dumps(past.json()["error"]["details"])
    assert trips_for(user_id) == []

    today = client.post(
        "/api/trip/preferences",
        headers=headers,
        json=payload(start_date=_today().isoformat(), end_date=(_today() + timedelta(days=2)).isoformat()),
    )
    assert today.status_code == 200, today.text


def test_stored_trips_with_past_dates_still_load():
    old = TripPreferences(**{**payload(), "start_date": "2026-01-10", "end_date": "2026-01-12"})
    assert old.start_date == date(2026, 1, 10)


@pytest.mark.parametrize("raw_budget", ["Infinity", "-Infinity", "NaN", "1e400", '"inf"', '"nan"'])
def test_non_finite_budget_is_rejected(api, monkeypatch, raw_budget):
    client, headers, user_id = api
    stub_feasibility(monkeypatch, "ok")
    body = json.dumps(payload()).replace('"budget_amount": 3200', f'"budget_amount": {raw_budget}')
    assert raw_budget in body

    response = client.post(
        "/api/trip/preferences",
        headers={**headers, "Content-Type": "application/json"},
        content=body,
    )

    assert response.status_code == 422, response.text
    assert trips_for(user_id) == []


def test_budget_gt_zero_alone_would_let_infinity_through():
    with pytest.raises(ValidationError, match="finite"):
        _prefs(budget_amount=float("inf"))
    with pytest.raises(ValidationError, match="finite"):
        _prefs(budget_amount="inf")
    with pytest.raises(ValidationError):
        _prefs(budget_amount=float("nan"))


def test_prompt_bound_inputs_have_maximum_lengths():
    with pytest.raises(ValidationError, match="at most 120"):
        _prefs(destination="x" * 121)
    with pytest.raises(ValidationError, match="at most 120"):
        _prefs(origin="x" * 121)
    with pytest.raises(ValidationError, match="at most 80"):
        _prefs(cotravellers=["y" * 81])
    with pytest.raises(ValidationError, match="at most 11"):
        _prefs(vibes=["culture"] * 12)
    with pytest.raises(ValidationError, match="at most 12"):
        _prefs(currency="U" * 13)
    with pytest.raises(ValidationError, match="at most 2000"):
        ChatInput(message="m" * 2001)
    assert _prefs(destination="x" * 120).destination == "x" * 120


def recommendation(**overrides) -> Recommendation:
    values = dict(
        name="Cafe",
        category="restaurant",
        description="A quiet cafe.",
        reasoning="Fits the profile.",
        estimated_cost="$10-$20",
        cost_min=10,
        cost_max=20,
        rating=4.5,
        location="Gion",
        image_search_query="kyoto cafe",
    )
    values.update(overrides)
    return Recommendation(**values)


def test_recommendation_rejects_non_finite_numbers_and_orders_the_cost_range():
    for field in ("cost_min", "cost_max", "rating", "score"):
        with pytest.raises(ValidationError, match="finite"):
            recommendation(**{field: float("inf")})
        with pytest.raises(ValidationError):
            recommendation(**{field: float("nan")})
    reversed_range = recommendation(cost_min=80, cost_max=40)
    assert (reversed_range.cost_min, reversed_range.cost_max) == (40, 80)


def test_feasibility_timeout_at_the_api_returns_unchecked_inside_the_deadline(api, monkeypatch):
    client, headers, user_id = api
    deadline = 0.2
    monkeypatch.setattr(feasibility, "FEASIBILITY_TIMEOUT_SECONDS", deadline)

    async def hanging_model(*_args, **_kwargs):
        await asyncio.sleep(5)
        return '{"verdict": "ok"}'

    monkeypatch.setattr(feasibility, "generate_text", hanging_model)
    started = time.perf_counter()
    response = client.post(
        "/api/trip/preferences", headers={**headers, "Idempotency-Key": KEY}, json=payload()
    )
    elapsed = time.perf_counter() - started

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "received"
    assert response.json()["feasibility"]["verdict"] == "unchecked"
    assert elapsed < deadline + 0.5
    assert len(trips_for(user_id)) == 1


def test_concurrent_duplicate_insert_resolves_to_the_existing_trip(tmp_path, monkeypatch):
    database_path = tmp_path / "race.db"
    monkeypatch.setattr(db, "DB_PATH", database_path)
    db.dispose_engine()
    db.init_db()
    db.create_user("u1", "race@example.com", "salt", "hash", datetime.now(timezone.utc).isoformat())

    winner = create_trip(_prefs(), "u1", idempotency_key=KEY)
    loser = create_trip(_prefs(), "u1", idempotency_key=KEY)

    assert loser.trip_id == winner.trip_id
    assert len(trips_for("u1")) == 1
    with pytest.raises(db.TripIdempotencyConflict):
        db.save_trip_state({**winner.model_dump(mode="json"), "trip_id": "another-trip-id"})
    assert len(trips_for("u1")) == 1


def test_production_refuses_the_sqlite_fallback(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        db._database_url()

    monkeypatch.setenv("DATABASE_URL", "postgres://user:pw@db.example/checkin")
    assert db._database_url() == "postgresql+psycopg://user:pw@db.example/checkin"

    monkeypatch.delenv("DATABASE_URL")
    monkeypatch.setenv("APP_ENV", "development")
    assert db._database_url().startswith("sqlite+pysqlite:///")


def test_alembic_head_check_raises_on_mismatch_and_passes_on_match(monkeypatch):
    monkeypatch.setattr(db, "_script_head", lambda: "20260901_0004")
    monkeypatch.setattr(db, "_recorded_revision", lambda _engine: "20260827_0003")
    with pytest.raises(RuntimeError, match="20260827_0003.*20260901_0004"):
        db._verify_alembic_head(object())

    monkeypatch.setattr(db, "_recorded_revision", lambda _engine: None)
    with pytest.raises(RuntimeError, match="none"):
        db._verify_alembic_head(object())

    monkeypatch.setattr(db, "_recorded_revision", lambda _engine: "20260901_0004")
    db._verify_alembic_head(object())


def test_alembic_script_head_resolves_from_any_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    head = db._script_head()
    assert head is not None
    assert re.fullmatch(r"\d{8}_\d{4}", head)
    assert head >= "20260901_0004"
