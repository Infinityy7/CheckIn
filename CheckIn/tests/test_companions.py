"""Companion consent: an accepted invitation is the only key to another account's profile."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import companions
import db
import main
import profiles

MEMBER_SENTENCE = "Quietly obsessed with midnight ramen counters and dawn temple walks."
MEMBER_SKETCH = f"""# Character Sketch
keywords: ramen, temples

```json
{{"likes":{{"ramen":3}},"dislikes":{{}},"diet":["vegetarian"],"pace":"slow","vibe_weights":{{"food":0.7,"culture":0.3}}}}
```

{MEMBER_SENTENCE}
"""

ORGANIZER_SKETCH = """# Character Sketch
keywords: markets

```json
{"likes":{"markets":2},"dislikes":{},"diet":[],"pace":"moderate"}
```

Loves markets and unhurried mornings.
"""


@pytest.fixture
def client(tmp_path):
    db.DB_PATH = tmp_path / "companions.db"
    db.dispose_engine()
    db.init_db()
    return TestClient(main.app)


def register(client: TestClient, email: str, username: str, name: str | None = None) -> tuple[dict, str]:
    payload = {"email": email, "password": "safe-password-1", "username": username}
    if name:
        payload["name"] = name
    response = client.post("/api/auth/register", json=payload)
    assert response.status_code == 200, response.text
    headers = {"Authorization": f"Bearer {response.json()['token']}"}
    return headers, db.get_user_by_email(email)["user_id"]


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


def invite(client: TestClient, headers: dict, username: str) -> dict:
    response = client.post("/api/companions/links", headers=headers, json={"username": username})
    assert response.status_code == 200, response.text
    return response.json()


def respond(client: TestClient, headers: dict, link_id: str, action: str) -> dict:
    response = client.post(f"/api/companions/links/{link_id}/{action}", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def stub_research(monkeypatch) -> dict:
    captured: dict = {}

    async def fake_brief(_prefs, _sketch, cotraveller_sketches):
        captured["sketches"] = list(cotraveller_sketches or [])
        return "A shared brief."

    async def fake_agents(_prefs, _brief, _user_taste, cotraveller_tastes, **_kwargs):
        captured["tastes"] = list(cotraveller_tastes or [])
        yield {"event": "all_complete", "status": "complete"}

    monkeypatch.setattr(main, "generate_context_brief", fake_brief)
    monkeypatch.setattr(main, "run_agents_streaming", fake_agents)
    return captured


@pytest.fixture
def party(client):
    organizer, organizer_id = register(client, "organizer@example.com", "organizer", "Ora Ganizer")
    member, member_id = register(client, "member@example.com", "member", "Mem Ber")
    profiles.save_sketch(organizer_id, ORGANIZER_SKETCH, ["Markets"])
    profiles.save_sketch(member_id, MEMBER_SKETCH, ["Ramen"])
    return {
        "organizer": organizer, "organizer_id": organizer_id,
        "member": member, "member_id": member_id,
    }


def test_unrelated_user_cannot_attach_another_accounts_profile(client, party):
    lookup = client.get("/api/users/lookup", params={"username": "member"}, headers=party["organizer"])
    assert lookup.status_code == 200
    assert lookup.json()["link_status"] == "none"
    assert lookup.json()["intake_complete"] is True

    created = client.post(
        "/api/trip/preferences",
        headers=party["organizer"],
        json=trip_payload(cotraveller_usernames=["member"]),
    )
    assert created.status_code == 403
    assert "@member" in created.json()["detail"]
    assert "invitation" in created.json()["detail"]

    sketches, tastes = companions.linked_companion_profiles(party["organizer_id"], ["member"])
    assert sketches == [] and tastes == []

    pending = invite(client, party["organizer"], "member")
    assert pending["status"] == "pending"
    still_blocked = client.post(
        "/api/trip/preferences",
        headers=party["organizer"],
        json=trip_payload(cotraveller_usernames=["member"]),
    )
    assert still_blocked.status_code == 403
    assert companions.linked_companion_profiles(party["organizer_id"], ["member"]) == ([], [])


def test_withdrawn_consent_never_reaches_the_research_prompt(client, party, monkeypatch):
    captured = stub_research(monkeypatch)
    link = invite(client, party["organizer"], "member")
    respond(client, party["member"], link["link_id"], "accept")
    created = client.post(
        "/api/trip/preferences",
        headers=party["organizer"],
        json=trip_payload(cotraveller_usernames=["member"]),
    )
    assert created.status_code == 200, created.text
    trip_id = created.json()["trip_id"]

    # the member declines after the trip exists: their profile must vanish from research
    respond(client, party["member"], link["link_id"], "decline")
    research = client.post(f"/api/trip/{trip_id}/research", headers=party["organizer"])
    assert research.status_code == 200
    assert captured["sketches"] == []
    assert captured["tastes"] == []
    assert MEMBER_SENTENCE not in research.text

    # a fresh invitation is only pending: still nothing
    assert invite(client, party["organizer"], "member")["status"] == "pending"
    research = client.post(f"/api/trip/{trip_id}/research", headers=party["organizer"])
    assert research.status_code == 200
    assert captured["sketches"] == []
    assert captured["tastes"] == []

    # the organizer revoking has the same effect as a decline
    respond(client, party["member"], link["link_id"], "accept")
    revoked = client.delete(f"/api/companions/links/{link['link_id']}", headers=party["organizer"])
    assert revoked.status_code == 200 and revoked.json()["status"] == "revoked"
    research = client.post(f"/api/trip/{trip_id}/research", headers=party["organizer"])
    assert captured["tastes"] == []


def test_accepted_link_contributes_taste_without_returning_the_sketch(client, party, monkeypatch):
    captured = stub_research(monkeypatch)
    link = invite(client, party["organizer"], "member")
    accepted = respond(client, party["member"], link["link_id"], "accept")
    assert accepted["status"] == "accepted"
    assert accepted["username"] == "organizer"

    lookup = client.get("/api/users/lookup", params={"username": "member"}, headers=party["organizer"])
    assert lookup.json()["link_status"] == "accepted"

    created = client.post(
        "/api/trip/preferences",
        headers=party["organizer"],
        json=trip_payload(cotraveller_usernames=["member"]),
    )
    assert created.status_code == 200, created.text
    trip_id = created.json()["trip_id"]

    research = client.post(f"/api/trip/{trip_id}/research", headers=party["organizer"])
    assert research.status_code == 200
    assert '"event": "all_complete"' in research.text
    assert captured["tastes"] and captured["tastes"][0]["diet"] == ["vegetarian"]
    assert captured["tastes"][0]["likes"] == {"ramen": 3}
    # the shared brief is echoed back to the organizer, so linked prose stays out of it
    assert captured["sketches"] == []

    links = client.get("/api/companions/links", headers=party["organizer"])
    trip = client.get(f"/api/trip/{trip_id}", headers=party["organizer"])
    for response in (lookup, created, research, links, trip):
        assert response.status_code == 200
        assert MEMBER_SENTENCE not in response.text
        assert "ramen" not in response.text.lower()
    assert links.json()["outgoing"][0] == {
        "link_id": link["link_id"],
        "username": "member",
        "name": "Mem Ber",
        "status": "accepted",
        "created_at": links.json()["outgoing"][0]["created_at"],
        "responded_at": links.json()["outgoing"][0]["responded_at"],
    }
    assert links.json()["outgoing"][0]["responded_at"] is not None


def test_invitation_lifecycle_and_authorization(client, party):
    organizer, member = party["organizer"], party["member"]
    outsider, _ = register(client, "outsider@example.com", "outsider")

    assert client.post("/api/companions/links", headers=organizer, json={"username": "organizer"}).status_code == 400
    assert client.post("/api/companions/links", headers=organizer, json={"username": "ghost"}).status_code == 404
    assert client.post("/api/companions/links", json={"username": "member"}).status_code == 401

    link = invite(client, organizer, "@Member")
    assert link["status"] == "pending"
    assert link["username"] == "member"
    assert link["responded_at"] is None
    assert invite(client, organizer, "member")["link_id"] == link["link_id"]

    incoming = client.get("/api/companions/links", headers=member).json()
    assert [row["username"] for row in incoming["incoming"]] == ["organizer"]
    assert incoming["incoming"][0]["name"] == "Ora Ganizer"
    assert incoming["outgoing"] == []
    outgoing = client.get("/api/companions/links", headers=organizer).json()
    assert [row["status"] for row in outgoing["outgoing"]] == ["pending"]
    assert outgoing["incoming"] == []

    # only the invitee may respond; unknown links are 404
    assert client.post(f"/api/companions/links/{link['link_id']}/accept", headers=organizer).status_code == 403
    assert client.post(f"/api/companions/links/{link['link_id']}/accept", headers=outsider).status_code == 403
    assert client.post("/api/companions/links/00000000-0000-0000-0000-000000000000/accept", headers=member).status_code == 404
    assert client.delete(f"/api/companions/links/{link['link_id']}", headers=outsider).status_code == 403
    assert client.delete("/api/companions/links/00000000-0000-0000-0000-000000000000", headers=member).status_code == 404

    accepted = respond(client, member, link["link_id"], "accept")
    assert accepted["status"] == "accepted" and accepted["responded_at"]
    assert db.companion_link_status(party["organizer_id"], party["member_id"]) == "accepted"
    assert db.companion_link_status(party["member_id"], party["organizer_id"]) == "none"
    assert invite(client, organizer, "member")["status"] == "accepted"

    declined = respond(client, member, link["link_id"], "decline")
    assert declined["status"] == "declined"
    lookup = client.get("/api/users/lookup", params={"username": "member"}, headers=organizer).json()
    assert lookup["link_status"] == "declined"

    reinvited = invite(client, organizer, "member")
    assert reinvited["link_id"] == link["link_id"]
    assert reinvited["status"] == "pending"
    assert reinvited["responded_at"] is None

    # the invitee removing the link declines it; the inviter removing it revokes it
    removed_by_member = client.delete(f"/api/companions/links/{link['link_id']}", headers=member).json()
    assert removed_by_member["status"] == "declined"
    respond(client, member, invite(client, organizer, "member")["link_id"], "accept")
    revoked = client.delete(f"/api/companions/links/{link['link_id']}", headers=organizer).json()
    assert revoked["status"] == "revoked"
    assert client.get("/api/users/lookup", params={"username": "member"}, headers=organizer).json()["link_status"] == "revoked"
    assert invite(client, organizer, "member")["status"] == "pending"


def test_companion_link_storage_invariants(tmp_path):
    db.DB_PATH = tmp_path / "links.db"
    db.dispose_engine()
    db.init_db()
    with pytest.raises(ValueError):
        db.create_or_reset_companion_link("same", "same")
    with pytest.raises(ValueError):
        db.respond_companion_link("missing", "someone", "revoked")
    assert db.get_companion_link_by_id("missing") is None
    assert db.respond_companion_link("missing", "someone", "accepted") is None
    assert db.delete_companion_link("missing", "someone") is None
    assert db.companion_link_status("a", "b") == "none"


def test_only_pending_invitations_can_be_answered(client, party):
    organizer, member = party["organizer"], party["member"]
    link = invite(client, organizer, "member")

    assert client.delete(f"/api/companions/links/{link['link_id']}", headers=organizer).status_code == 200
    revived = client.post(f"/api/companions/links/{link['link_id']}/accept", headers=member)
    assert revived.status_code == 409
    assert db.get_companion_link_by_id(link["link_id"])["status"] == "revoked"
    assert db.companion_link_status(party["organizer_id"], party["member_id"]) == "revoked"

    again = invite(client, organizer, "member")
    assert again["link_id"] == link["link_id"]
    assert again["status"] == "pending"
    assert respond(client, member, link["link_id"], "accept")["status"] == "accepted"
    assert client.post(f"/api/companions/links/{link['link_id']}/accept", headers=member).status_code == 409
    assert respond(client, member, link["link_id"], "decline")["status"] == "declined"
    assert client.post(f"/api/companions/links/{link['link_id']}/accept", headers=member).status_code == 409
    assert client.post(f"/api/companions/links/{link['link_id']}/decline", headers=member).status_code == 409
    assert db.companion_link_status(party["organizer_id"], party["member_id"]) == "declined"
