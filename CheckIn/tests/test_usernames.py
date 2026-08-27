"""Username identity: registration, login, lookup, and co-traveller guards."""

from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db
import main
import profiles
from schemas import GroupType, TripPreferences

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
def client(tmp_path):
    db.DB_PATH = tmp_path / "usernames.db"
    db.dispose_engine()
    db.init_db()
    return TestClient(main.app)


def register(client: TestClient, email: str, **extra) -> dict[str, str]:
    response = client.post(
        "/api/auth/register",
        json={"email": email, "password": "safe-password-1", **extra},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def complete_intake(client: TestClient, headers: dict[str, str], monkeypatch) -> None:
    async def deterministic_polish(*_args, **_kwargs):
        return "A calm, curious traveler drawn to nature, local moments, and unhurried discovery."

    monkeypatch.setattr(profiles, "generate_text", deterministic_polish)
    for question_id, value in QUESTION_ANSWERS.items():
        response = client.put(
            f"/api/profile/intake/answers/{question_id}",
            headers=headers,
            json={"value": value},
        )
        assert response.status_code == 200, response.text
    response = client.post("/api/profile/intake/complete", headers=headers)
    assert response.status_code == 200, response.text


def trip_payload(**overrides) -> dict:
    payload = {
        "destination": "Kyoto",
        "origin": "Mumbai",
        "start_date": "2026-10-12",
        "end_date": "2026-10-15",
        "budget_amount": 2500,
        "currency": "USD",
        "vibes": ["culture", "food"],
        "group_type": "couple",
        "num_travelers": 2,
        "cotravellers": [],
        "cotraveller_usernames": [],
    }
    payload.update(overrides)
    return payload


def test_register_with_full_details_normalizes_username(client):
    headers = register(
        client,
        "casey@example.com",
        username="@Casey_T",
        name="Casey Traveler",
        phone="+1 (415) 555-0100",
    )
    me = client.get("/api/auth/me", headers=headers).json()
    assert me["username"] == "casey_t"
    assert me["name"] == "Casey Traveler"
    assert me["phone"] == "+1 (415) 555-0100"
    assert me["intake_complete"] is False


def test_legacy_register_derives_username_from_email(client):
    first = register(client, "flow.fan@example.com")
    assert client.get("/api/auth/me", headers=first).json()["username"] == "flow-fan"

    # a second account with the same local part gets a deduped handle
    second = register(client, "flow.fan@other.org")
    assert client.get("/api/auth/me", headers=second).json()["username"] == "flow-fan-2"


def test_duplicate_and_invalid_registration_inputs(client):
    register(client, "original@example.com", username="wanderer")

    duplicate = client.post(
        "/api/auth/register",
        json={"email": "copycat@example.com", "password": "safe-password-1", "username": "@Wanderer"},
    )
    assert duplicate.status_code == 409
    assert "already taken" in duplicate.json()["detail"]

    bad_username = client.post(
        "/api/auth/register",
        json={"email": "badname@example.com", "password": "safe-password-1", "username": "Bad Name!"},
    )
    assert bad_username.status_code == 422
    assert bad_username.json()["error"]["code"] == "VALIDATION_ERROR"

    bad_phone = client.post(
        "/api/auth/register",
        json={"email": "badphone@example.com", "password": "safe-password-1", "phone": "not-a-phone"},
    )
    assert bad_phone.status_code == 422


def test_login_by_username_or_email(client):
    register(client, "login@example.com", username="login-user")

    for identifier in ["@login-user", "Login-User", "@LOGIN-USER", "login@example.com"]:
        response = client.post(
            "/api/auth/login",
            json={"identifier": identifier, "password": "safe-password-1"},
        )
        assert response.status_code == 200, f"{identifier}: {response.text}"
        assert response.json()["token"]

    # the pre-username body shape keeps working
    legacy = client.post(
        "/api/auth/login",
        json={"email": "login@example.com", "password": "safe-password-1"},
    )
    assert legacy.status_code == 200

    wrong = client.post(
        "/api/auth/login",
        json={"identifier": "login-user", "password": "wrong-password-1"},
    )
    assert wrong.status_code == 401


def test_user_lookup(client):
    headers = register(client, "finder@example.com", username="finder")
    register(client, "target@example.com", username="target-user", name="Target Person")

    found = client.get(
        "/api/users/lookup", params={"username": "@Target-User"}, headers=headers
    )
    assert found.status_code == 200
    assert found.json() == {
        "username": "target-user",
        "name": "Target Person",
        "intake_complete": False,
    }

    missing = client.get(
        "/api/users/lookup", params={"username": "ghost-user"}, headers=headers
    )
    assert missing.status_code == 404
    assert "No CheckIn user" in missing.json()["detail"]

    unauthenticated = client.get("/api/users/lookup", params={"username": "target-user"})
    assert unauthenticated.status_code == 401


def test_trip_creation_guards_username_cotravellers(client, monkeypatch):
    owner_headers = register(client, "owner@example.com", username="trip-owner")
    complete_intake(client, owner_headers, monkeypatch)
    buddy_headers = register(client, "buddy@example.com", username="b_user")

    incomplete_buddy = client.post(
        "/api/trip/preferences",
        headers=owner_headers,
        json=trip_payload(cotraveller_usernames=["b_user"]),
    )
    assert incomplete_buddy.status_code == 400
    assert "taste profile" in incomplete_buddy.json()["detail"]

    unknown = client.post(
        "/api/trip/preferences",
        headers=owner_headers,
        json=trip_payload(cotraveller_usernames=["ghost-user"]),
    )
    assert unknown.status_code == 400
    assert "No CheckIn user" in unknown.json()["detail"]

    themselves = client.post(
        "/api/trip/preferences",
        headers=owner_headers,
        json=trip_payload(cotraveller_usernames=["@Trip-Owner"]),
    )
    assert themselves.status_code == 400
    assert "yourself" in themselves.json()["detail"]

    complete_intake(client, buddy_headers, monkeypatch)
    created = client.post(
        "/api/trip/preferences",
        headers=owner_headers,
        json=trip_payload(cotraveller_usernames=["@B_User", "b_user"]),
    )
    assert created.status_code == 200, created.text
    assert created.json()["preferences"]["cotraveller_usernames"] == ["b_user"]

    trip_id = created.json()["trip_id"]
    saved = client.get(f"/api/trip/{trip_id}", headers=owner_headers)
    assert saved.status_code == 200
    assert saved.json()["preferences"]["cotraveller_usernames"] == ["b_user"]


def test_trip_preferences_rejects_more_than_eight_companions(client):
    headers = register(client, "crowded@example.com", username="crowd-lead")
    response = client.post(
        "/api/trip/preferences",
        headers=headers,
        json=trip_payload(
            cotravellers=["g1", "g2", "g3", "g4", "g5"],
            cotraveller_usernames=["u1", "u2", "u3", "u4"],
        ),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_trip_preferences_normalizes_and_dedupes_usernames():
    prefs = TripPreferences(
        destination="Kyoto",
        origin="Mumbai",
        start_date=date(2026, 10, 12),
        end_date=date(2026, 10, 15),
        budget_amount=2500,
        currency="USD",
        vibes=["culture"],
        group_type=GroupType.COUPLE,
        num_travelers=2,
        cotraveller_usernames=["@B_User", "b_user", "OTHER", "  "],
    )
    assert prefs.cotraveller_usernames == ["b_user", "other"]

    with pytest.raises(ValidationError):
        TripPreferences(
            destination="Kyoto",
            origin="Mumbai",
            start_date=date(2026, 10, 12),
            end_date=date(2026, 10, 15),
            budget_amount=2500,
            currency="USD",
            vibes=["culture"],
            group_type=GroupType.COUPLE,
            num_travelers=2,
            cotraveller_usernames=["Bad Name!"],
        )


def test_sqlite_backfill_derives_usernames_for_legacy_rows(tmp_path):
    db.DB_PATH = tmp_path / "legacy.db"
    db.dispose_engine()
    db.init_db()

    now = datetime.now(timezone.utc).isoformat()
    db.create_user("legacy-1", "old.timer@example.com", "00" * 16, "ab" * 32, now, username=None)
    db.create_user("legacy-2", "old.timer@other.org", "11" * 16, "cd" * 32, now, username=None)

    # a fresh startup over the old rows fills usernames in from the email
    db.init_db()
    assert db.get_user_by_email("old.timer@example.com")["username"] == "old-timer"
    assert db.get_user_by_email("old.timer@other.org")["username"] == "old-timer-2"
    assert db.get_user_by_username("@Old-Timer")["user_id"] == "legacy-1"


def test_normalize_username_edge_cases():
    assert db.normalize_username("@Weird Name!") == "weird-name"
    assert db.normalize_username("") == ""
    assert db.normalize_username("  UPPER_case-9  ") == "upper_case-9"
    assert len(db.normalize_username("a" * 45)) == 30
