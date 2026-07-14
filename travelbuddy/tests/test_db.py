"""Tests for the sqlite persistence layer."""

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db


def fresh_db(tmp_path):
    # point the module at a scratch db and force a new connection
    db.DB_PATH = tmp_path / "t.db"
    db._conn = None
    db.init_db()


def test_user_roundtrip(tmp_path):
    fresh_db(tmp_path)

    db.create_user("u1", "a@example.com", "salt1", "hash1", "2026-01-01T00:00:00+00:00")

    by_email = db.get_user_by_email("a@example.com")
    assert by_email is not None
    assert by_email["user_id"] == "u1"
    assert by_email["pw_salt"] == "salt1"
    assert by_email["pw_hash"] == "hash1"

    by_id = db.get_user_by_id("u1")
    assert by_id is not None
    assert by_id["email"] == "a@example.com"

    # unknown lookups come back as None
    assert db.get_user_by_email("nobody@example.com") is None
    assert db.get_user_by_id("nope") is None

    # duplicate email should blow up
    try:
        db.create_user("u2", "a@example.com", "salt2", "hash2", "2026-01-02T00:00:00+00:00")
        assert False, "duplicate email should have raised"
    except sqlite3.IntegrityError:
        pass

    users = db.all_users()
    assert len(users) == 1


def test_profile_roundtrip_and_upsert(tmp_path):
    fresh_db(tmp_path)

    taste = json.dumps({"keywords": ["street food", "temples"]})
    db.save_profile("u1", "self", "self", "self", "# my sketch", taste)

    prof = db.get_profile("u1", "self")
    assert prof is not None
    assert prof["sketch_md"] == "# my sketch"
    assert json.loads(prof["taste_json"]) == {"keywords": ["street food", "temples"]}
    assert prof["updated_at"]

    # saving again replaces the row instead of adding a second one
    db.save_profile("u1", "self", "self", "self", "# updated sketch", None)
    prof2 = db.get_profile("u1", "self")
    assert prof2["sketch_md"] == "# updated sketch"
    assert prof2["taste_json"] is None

    # missing profile is None
    assert db.get_profile("u1", "cotraveller", "mom") is None


def test_list_cotraveller_profiles_ordering(tmp_path):
    fresh_db(tmp_path)

    # insert out of order on purpose
    db.save_profile("u1", "cotraveller", "zoe", "zoe", "sketch z", None)
    db.save_profile("u1", "cotraveller", "adam", "adam", "sketch a", None)
    db.save_profile("u1", "cotraveller", "mom", "mom", "sketch m", None)
    # another user's rows should not leak in
    db.save_profile("u2", "cotraveller", "bob", "bob", "sketch b", None)
    # nor should the self profile
    db.save_profile("u1", "self", "self", "self", "me", None)

    profs = db.list_cotraveller_profiles("u1")
    slugs = []
    for p in profs:
        slugs.append(p["slug"])
    assert slugs == ["adam", "mom", "zoe"]


def test_delete_profile_is_scoped(tmp_path):
    fresh_db(tmp_path)
    db.save_profile("u1", "self", "self", "self", "one", None)
    db.save_profile("u2", "self", "self", "self", "two", None)
    assert db.delete_profile("u1", "self") is True
    assert db.get_profile("u1", "self") is None
    assert db.get_profile("u2", "self")["sketch_md"] == "two"
    assert db.delete_profile("u1", "self") is False


def test_legacy_migration(tmp_path):
    # build a fake pre-db data dir
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    users_json = {
        "old@example.com": {
            "user_id": "olduid",
            "email": "old@example.com",
            "pw_salt": "oldsalt",
            "pw_hash": "oldhash",
            "created_at": "2025-12-01T00:00:00+00:00",
        }
    }
    (data_dir / "users.json").write_text(json.dumps(users_json))

    user_dir = data_dir / "users" / "olduid"
    user_dir.mkdir(parents=True)
    (user_dir / "character.md").write_text("# old character sketch")
    cot_dir = user_dir / "cotravellers"
    cot_dir.mkdir()
    (cot_dir / "mom.md").write_text("# mom sketch")

    # db file lives in the same data dir so the data root resolves there
    db.DB_PATH = data_dir / "travelbuddy.db"
    db._conn = None
    db.init_db()

    # user row made it over
    user = db.get_user_by_email("old@example.com")
    assert user is not None
    assert user["user_id"] == "olduid"
    assert user["pw_hash"] == "oldhash"

    # self profile made it over
    prof = db.get_profile("olduid", "self")
    assert prof is not None
    assert prof["sketch_md"] == "# old character sketch"
    assert prof["name"] == "self"

    # cotraveller made it over
    mom = db.get_profile("olduid", "cotraveller", "mom")
    assert mom is not None
    assert mom["sketch_md"] == "# mom sketch"

    # users.json got renamed so it never re-runs
    assert not (data_dir / "users.json").exists()
    assert (data_dir / "users.json.migrated").exists()

    # md files are left in place, harmless leftovers
    assert (user_dir / "character.md").exists()

    # re-running init_db is safe and doesn't duplicate anything
    db.init_db()
    assert len(db.all_users()) == 1
