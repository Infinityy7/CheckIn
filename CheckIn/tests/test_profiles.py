"""Tests for profile parsing, auth basics, and personalized ranking."""

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import auth
import db
from profiles import (
    _taste_from_keywords,
    get_character_profile,
    parse_keywords,
    parse_taste,
    reset_character_profile,
    save_sketch,
    slugify,
    update_character_profile,
)
from schemas import GroupType, Recommendation, TripPreferences
from tastes import taste_score

SAMPLE_SKETCH = """# Character Sketch
keywords: street food, temples, slow mornings, vegetarian

Loves wandering markets and eating everything on a stick.
"""

SKETCH_WITH_TASTE = """# Character Sketch
keywords: street food, temples

```json
{"likes": {"street food": 3, "temples": 2}, "dislikes": {"crowds": 3}, "diet": ["vegetarian"], "pace": "slow"}
```

Loves wandering markets.
"""


def make_prefs(vibes):
    return TripPreferences(
        destination="Tokyo",
        origin="Mumbai",
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 5),
        budget_amount=2000,
        currency="USD",
        vibes=vibes,
        group_type=GroupType.COUPLE,
        num_travelers=2,
    )


def make_rec(**overrides):
    fields = {
        "name": "Test Place",
        "category": "restaurant",
        "description": "A lovely place.",
        "reasoning": "Great fit.",
        "estimated_cost": "$20-$40",
        "cost_min": 20,
        "cost_max": 40,
        "rating": 4.5,
        "review_count": 500,
        "location": "Shibuya, Tokyo",
        "image_search_query": "test place tokyo",
    }
    fields.update(overrides)
    return Recommendation(**fields)


def test_parse_keywords_reads_the_line():
    assert parse_keywords(SAMPLE_SKETCH) == [
        "street food", "temples", "slow mornings", "vegetarian",
    ]


def test_parse_keywords_handles_missing_line():
    assert parse_keywords("just some prose, no tags") == []


def test_slugify():
    assert slugify("My Mom!") == "my-mom"
    assert slugify("  ") == "someone"


def test_parse_taste_splits_json_block():
    clean, taste = parse_taste(SKETCH_WITH_TASTE)
    assert taste is not None
    assert taste["likes"]["street food"] == 3
    assert taste["diet"] == ["vegetarian"]
    assert "```json" not in clean
    assert "Loves wandering markets." in clean


def test_parse_taste_survives_missing_block():
    clean, taste = parse_taste(SAMPLE_SKETCH)
    assert taste is None
    assert "Loves wandering" in clean


def test_keyword_fallback_taste():
    # migrated old sketches get a rough taste from the keywords line
    taste = _taste_from_keywords(SAMPLE_SKETCH)
    assert taste["likes"]["street food"] == 2
    assert "vegetarian" in taste["diet"]


def test_taste_distinguishes_recs():
    # Structured tags, never generated prose, separate candidates.
    taste = {"vibe_weights": {"culture": 0.8, "shopping": 0.2}}
    temple_walk = make_rec(name="Old Temple Walk", vibe_tags=["culture"])
    mall_trip = make_rec(name="Mega Mall", vibe_tags=["shopping"])
    temple_score = taste_score(temple_walk, taste)[0]
    mall_score = taste_score(mall_trip, taste)[0]
    assert temple_score > mall_score


def test_auth_register_login_roundtrip(tmp_path):
    # point the db at a scratch file so the test doesn't touch real data
    db.DB_PATH = tmp_path / "t.db"
    db._conn = None
    db.init_db()
    token = auth.register("test@example.com", "supersecret1")
    user_id = db.get_user_by_email("test@example.com")["user_id"]
    assert user_id

    # right password works, wrong one doesn't
    token2 = auth.login("test@example.com", "supersecret1")
    assert auth.get_current_user(f"Bearer {token2}") == user_id
    try:
        auth.login("test@example.com", "wrongpassword")
        assert False, "wrong password should have been rejected"
    except Exception:
        pass

    # passwords are not stored in plain text
    row = db.get_user_by_email("test@example.com")
    assert row["pw_hash"] != "supersecret1"
    assert "supersecret1" not in str(row)


def test_character_profile_contract_and_editing(tmp_path):
    db.DB_PATH = tmp_path / "profile.db"
    db._conn = None
    db.init_db()
    raw = """# Character Sketch
keywords: street food, quiet corners

```json
{"likes":{"street food":3},"dislikes":{"crowds":2},"diet":[],"pace":"slow","traits":{"pace":"slow","adventureLevel":0.8}}
```

An unhurried traveler who follows food and avoids crowded group experiences.
"""
    save_sketch("profile-user", raw, ["Slow mornings", "Street food", "Hidden corners"])

    profile = get_character_profile("profile-user")
    assert profile is not None
    assert profile["id"] == "character:profile-user"
    assert profile["summary"].startswith("An unhurried traveler")
    assert "keywords:" not in profile["summary"]
    assert profile["traits"]["pace"] == "slow"
    assert profile["traits"]["adventureLevel"] == 0.8
    assert profile["rawAnswers"] == ["Slow mornings", "Street food", "Hidden corners"]

    edited = update_character_profile(
        "profile-user",
        "A thoughtful traveler who now wants a little more momentum and local food.",
        traits={**profile["traits"], "pace": "fast", "localVsTourist": 0.9},
    )
    assert edited["traits"]["pace"] == "fast"
    assert "more momentum" in edited["summary"]

    assert reset_character_profile("profile-user") is True
    assert get_character_profile("profile-user") is None


# --- durable guest intake ---

def _intake_stub(calls: list, sketch: str):
    async def fake_generate(prompt, **kwargs):
        calls.append(prompt)
        if kwargs.get("system_instruction") is None:
            return sketch
        return f"Q{sum(1 for entry in calls if 'next single message' in entry)}?"
    return fake_generate


def test_guest_intake_survives_restart_and_replays_duplicate_turns(tmp_path, monkeypatch):
    import asyncio

    import profiles

    db.DB_PATH = tmp_path / "intake.db"
    db.dispose_engine()
    db.init_db()
    calls: list[str] = []
    monkeypatch.setattr(profiles, "generate_text", _intake_stub(calls, SKETCH_WITH_TASTE))
    owner = "guest-owner"

    reply, done = asyncio.run(profiles.chat_turn(owner, "", "Maya"))
    assert (reply, done) == ("Q1?", False)
    # the opener is idempotent: a second empty message replays the same question
    assert asyncio.run(profiles.chat_turn(owner, "", "Maya")) == ("Q1?", False)
    assert len(calls) == 1

    profiles._chats.clear()  # a restart or another worker loses nothing
    reply, done = asyncio.run(profiles.chat_turn(owner, "Slow mornings", "Maya", turn_key="turn-1"))
    assert (reply, done) == ("Q2?", False)
    before = len(calls)
    assert asyncio.run(profiles.chat_turn(owner, "Slow mornings", "Maya", turn_key="turn-1")) == ("Q2?", False)
    assert len(calls) == before

    transcript = profiles.guest_chat_transcript(owner, "Maya")
    assert [turn["from"] for turn in transcript["turns"]] == ["tavi", "user", "tavi"]
    assert transcript["turns"][1]["text"] == "Slow mornings"
    assert transcript["done"] is False

    # a failed model turn leaves the answer stored once; the retry continues instead of appending
    async def boom(*_args, **_kwargs):
        raise RuntimeError("model down")

    monkeypatch.setattr(profiles, "generate_text", boom)
    with pytest.raises(RuntimeError):
        asyncio.run(profiles.chat_turn(owner, "Street food", "Maya", turn_key="turn-2"))
    monkeypatch.setattr(profiles, "generate_text", _intake_stub(calls, SKETCH_WITH_TASTE))
    reply, done = asyncio.run(profiles.chat_turn(owner, "Street food", "Maya", turn_key="turn-2"))
    assert (reply, done) == ("Q3?", False)
    stored = db.get_profile_intake(owner, "cotraveller", "maya")["transcript"]
    assert [entry["content"] for entry in stored if entry["role"] == "user"] == ["Slow mornings", "Street food"]

    asyncio.run(profiles.chat_turn(owner, "Mid-range", "Maya", turn_key="turn-3"))
    reply, done = asyncio.run(profiles.chat_turn(owner, "Early bird", "Maya", turn_key="turn-4"))
    assert done is True
    assert profiles.load_cotraveller(owner, "Maya") is not None
    finished = profiles.guest_chat_transcript(owner, "Maya")
    assert finished["done"] is True
    assert len(finished["turns"]) == 8

    # replaying the final answer neither re-generates the sketch nor appends
    before = len(calls)
    assert asyncio.run(profiles.chat_turn(owner, "Early bird", "Maya", turn_key="turn-4")) == (
        profiles.INTAKE_DONE_MESSAGE, True,
    )
    assert asyncio.run(profiles.chat_turn(owner, "late answer", "Maya", turn_key="turn-9")) == (
        profiles.INTAKE_DONE_MESSAGE, True,
    )
    assert len(calls) == before
    assert len(profiles.guest_chat_transcript(owner, "Maya")["turns"]) == 8


def test_self_chat_dedupes_by_turn_key(monkeypatch, tmp_path):
    import asyncio

    import profiles

    db.DB_PATH = tmp_path / "self-chat.db"
    db.dispose_engine()
    db.init_db()
    calls: list[str] = []
    monkeypatch.setattr(profiles, "generate_text", _intake_stub(calls, SKETCH_WITH_TASTE))
    profiles._chats.clear()

    assert asyncio.run(profiles.chat_turn("self-user", "", None)) == ("Q1?", False)
    first = asyncio.run(profiles.chat_turn("self-user", "Packed days", turn_key="s1"))
    assert first == ("Q2?", False)
    assert asyncio.run(profiles.chat_turn("self-user", "Packed days", turn_key="s1")) == first
    assert len(calls) == 2
    assert sum(1 for entry in profiles._chats["self-user"] if entry["role"] == "user") == 1


def test_profile_chat_endpoints_persist_and_replay(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import main
    import profiles

    db.DB_PATH = tmp_path / "chat-api.db"
    db.dispose_engine()
    db.init_db()
    calls: list[str] = []
    monkeypatch.setattr(profiles, "generate_text", _intake_stub(calls, SKETCH_WITH_TASTE))
    client = TestClient(main.app)
    registered = client.post("/api/auth/register", json={"email": "chat@example.com", "password": "safe-password-1"})
    headers = {"Authorization": f"Bearer {registered.json()['token']}"}

    empty = client.get("/api/profile/chat", params={"cotraveller_name": "Ravi"}, headers=headers)
    assert empty.status_code == 200
    assert empty.json() == {"turns": [], "done": False}
    assert client.get("/api/profile/chat", headers=headers).status_code == 422

    opener = client.post("/api/profile/chat", headers=headers, json={"message": "", "cotraveller_name": "Ravi"})
    assert opener.json() == {"reply": "Q1?", "done": False}
    answer = {"message": "Slow and curious", "cotraveller_name": "Ravi", "turn_key": "abc-123"}
    first = client.post("/api/profile/chat", headers=headers, json=answer)
    assert first.json() == {"reply": "Q2?", "done": False}
    assert client.post("/api/profile/chat", headers=headers, json=answer).json() == first.json()
    assert len(calls) == 2

    profiles._chats.clear()
    transcript = client.get("/api/profile/chat", params={"cotraveller_name": "Ravi"}, headers=headers)
    assert transcript.json() == {
        "turns": [
            {"from": "tavi", "text": "Q1?"},
            {"from": "user", "text": "Slow and curious"},
            {"from": "tavi", "text": "Q2?"},
        ],
        "done": False,
    }
    too_long = client.post("/api/profile/chat", headers=headers, json={**answer, "turn_key": "x" * 129})
    assert too_long.status_code == 422


def test_self_chat_replays_the_completed_final_turn_without_a_second_sketch(monkeypatch, tmp_path):
    import asyncio

    import profiles

    db.DB_PATH = tmp_path / "self-chat-final.db"
    db.dispose_engine()
    db.init_db()
    calls: list[str] = []
    monkeypatch.setattr(profiles, "generate_text", _intake_stub(calls, SKETCH_WITH_TASTE))
    profiles._chats.clear()
    profiles._completed_self_turns.clear()
    auth.register("final@example.com", "safe-password-1")
    user_id = db.get_user_by_email("final@example.com")["user_id"]

    asyncio.run(profiles.chat_turn(user_id, "", None))
    for index in range(1, profiles.USER_QUESTIONS + 1):
        reply, done = asyncio.run(profiles.chat_turn(user_id, f"answer {index}", turn_key=f"k{index}"))
    assert done is True
    sketch_calls = len(calls)
    version = db.get_profile(user_id, "self")["version"]

    replay = asyncio.run(profiles.chat_turn(user_id, f"answer {profiles.USER_QUESTIONS}", turn_key=f"k{profiles.USER_QUESTIONS}"))
    assert replay == (reply, True)
    assert len(calls) == sketch_calls
    assert db.get_profile(user_id, "self")["version"] == version
    assert user_id not in profiles._chats

    profiles.reset_intake(user_id)
    assert user_id not in profiles._completed_self_turns


def test_slugify_keeps_distinct_non_ascii_names_apart():
    assert slugify("李雷") == "guest-34eb561f"
    assert slugify("Зоя") == "guest-44b54532"
    assert slugify("李雷") != slugify("Зоя")
    assert slugify("Zoë Müller") == "zo-m-ller"
    assert slugify("  ") == "someone"
