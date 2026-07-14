"""Tests for profile parsing, auth basics, and personalized ranking."""

import sys
from datetime import date
from pathlib import Path

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
    # the taste vector separates recs that trip vibes alone can't
    _clean, taste = parse_taste(SKETCH_WITH_TASTE)
    temple_walk = make_rec(name="Old Temple Walk", description="A quiet cultural temple stroll.")
    mall_trip = make_rec(name="Mega Mall", description="A big cultural shopping complex.")
    temple_score = taste_score(temple_walk, taste)[0]
    mall_score = taste_score(mall_trip, taste)[0]
    assert temple_score > mall_score


def test_auth_register_login_roundtrip(tmp_path):
    # point the db at a scratch file so the test doesn't touch real data
    db.DB_PATH = tmp_path / "t.db"
    db._conn = None
    db.init_db()
    auth._sessions = {}

    token = auth.register("test@example.com", "supersecret1")
    user_id = auth._sessions[token]
    assert user_id

    # right password works, wrong one doesn't
    token2 = auth.login("test@example.com", "supersecret1")
    assert auth._sessions[token2] == user_id
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
        {**profile["traits"], "pace": "fast", "localVsTourist": 0.9},
    )
    assert edited["traits"]["pace"] == "fast"
    assert "more momentum" in edited["summary"]

    assert reset_character_profile("profile-user") is True
    assert get_character_profile("profile-user") is None
