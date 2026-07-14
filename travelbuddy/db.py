"""sqlite persistence for users and profiles."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent / "data" / "travelbuddy.db"

# tiny prototype: one shared connection + a lock around writes.
# the app is async but every call here is tiny, so this is fine.
_lock = threading.Lock()
_conn = None


def _connect() -> sqlite3.Connection:
    # lazy singleton so tests can point DB_PATH somewhere else
    # and set _conn = None to get a fresh connection
    global _conn
    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
    return _conn


def init_db() -> None:
    """Create tables if missing, then import any legacy json/md files."""
    conn = _connect()
    with _lock:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                email TEXT UNIQUE NOT NULL,
                pw_salt TEXT NOT NULL,
                pw_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                slug TEXT NOT NULL,
                name TEXT NOT NULL,
                sketch_md TEXT NOT NULL,
                taste_json TEXT,
                updated_at TEXT NOT NULL,
                UNIQUE(user_id, kind, slug)
            )
            """
        )
        conn.commit()
    _migrate_legacy_files()


def create_user(user_id: str, email: str, pw_salt: str, pw_hash: str, created_at: str) -> None:
    """Insert a new user. Duplicate email raises sqlite3.IntegrityError."""
    conn = _connect()
    with _lock:
        conn.execute(
            "INSERT INTO users (user_id, email, pw_salt, pw_hash, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, email, pw_salt, pw_hash, created_at),
        )
        conn.commit()


def get_user_by_email(email: str) -> dict | None:
    conn = _connect()
    row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if row is None:
        return None
    return dict(row)


def get_user_by_id(user_id: str) -> dict | None:
    conn = _connect()
    row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    if row is None:
        return None
    return dict(row)


def all_users() -> list[dict]:
    conn = _connect()
    rows = conn.execute("SELECT * FROM users").fetchall()
    out = []
    for row in rows:
        out.append(dict(row))
    return out


def save_profile(user_id: str, kind: str, slug: str, name: str, sketch_md: str, taste_json: str | None) -> None:
    """Upsert a profile row (keyed on user_id + kind + slug)."""
    conn = _connect()
    now = datetime.now(timezone.utc).isoformat()
    with _lock:
        conn.execute(
            """
            INSERT OR REPLACE INTO profiles (user_id, kind, slug, name, sketch_md, taste_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, kind, slug, name, sketch_md, taste_json, now),
        )
        conn.commit()


def get_profile(user_id: str, kind: str, slug: str = "self") -> dict | None:
    conn = _connect()
    row = conn.execute(
        "SELECT * FROM profiles WHERE user_id = ? AND kind = ? AND slug = ?",
        (user_id, kind, slug),
    ).fetchone()
    if row is None:
        return None
    return dict(row)


def list_cotraveller_profiles(user_id: str) -> list[dict]:
    conn = _connect()
    rows = conn.execute(
        "SELECT * FROM profiles WHERE user_id = ? AND kind = 'cotraveller' ORDER BY slug",
        (user_id,),
    ).fetchall()
    out = []
    for row in rows:
        out.append(dict(row))
    return out


def _migrate_legacy_files() -> None:
    """One-time import of the old json/markdown layout into sqlite."""
    # the data root is wherever the db file lives, so tests can redirect it
    data_dir = DB_PATH.parent
    try:
        # 1. users.json -> users table
        users_file = data_dir / "users.json"
        if users_file.exists():
            raw = json.loads(users_file.read_text())
            conn = _connect()
            with _lock:
                for email, info in raw.items():
                    conn.execute(
                        "INSERT OR IGNORE INTO users (user_id, email, pw_salt, pw_hash, created_at) VALUES (?, ?, ?, ?, ?)",
                        (
                            info["user_id"],
                            info["email"],
                            info["pw_salt"],
                            info["pw_hash"],
                            info["created_at"],
                        ),
                    )
                conn.commit()
            # rename so we never import it twice
            users_file.rename(data_dir / "users.json.migrated")

        # 2. per-user markdown files -> profiles table
        users_dir = data_dir / "users"
        if users_dir.exists():
            for user_dir in users_dir.iterdir():
                if not user_dir.is_dir():
                    continue
                user_id = user_dir.name

                # character sketch -> self profile, but don't clobber a real row
                character_file = user_dir / "character.md"
                if character_file.exists():
                    existing = get_profile(user_id, "self", "self")
                    if existing is None:
                        save_profile(user_id, "self", "self", "self", character_file.read_text(), None)

                # cotraveller sketches
                cotravellers_dir = user_dir / "cotravellers"
                if cotravellers_dir.exists():
                    for md_file in cotravellers_dir.iterdir():
                        if md_file.suffix != ".md":
                            continue
                        slug = md_file.stem
                        existing = get_profile(user_id, "cotraveller", slug)
                        if existing is None:
                            save_profile(user_id, "cotraveller", slug, slug, md_file.read_text(), None)
    except Exception as exc:
        # a broken legacy file must never block startup
        logger.warning("legacy data migration failed: %s", exc)
