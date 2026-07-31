#!/usr/bin/env python3
"""Import the legacy TravelBuddy SQLite users/profiles into PostgreSQL.

Run Alembic first. The import is transactional and idempotent; rerunning it
updates the same profile keys without creating duplicates.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.dialects.postgresql import insert

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database_models import Profile, User  # noqa: E402


PROFILE_NAMESPACE = uuid.UUID("96753760-0132-47d1-93a5-3ca479bc8a10")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite-path", type=Path, default=Path("data/travelbuddy.db"))
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def postgres_url(value: str) -> str:
    if value.startswith("postgres://"):
        return "postgresql+psycopg://" + value[len("postgres://"):]
    if value.startswith("postgresql://"):
        return "postgresql+psycopg://" + value[len("postgresql://"):]
    return value


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def valid_uuid(value: str, label: str) -> str:
    try:
        return str(uuid.UUID(value))
    except ValueError as exc:
        raise ValueError(f"{label} is not a UUID: {value!r}") from exc


def load_source(path: Path) -> tuple[list[dict], list[dict]]:
    if not path.exists():
        raise FileNotFoundError(path)
    uri = f"file:{path.resolve()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        names = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        missing = {"users", "profiles"} - names
        if missing:
            raise ValueError(f"SQLite source is missing tables: {sorted(missing)}")
        users = [dict(row) for row in connection.execute("SELECT * FROM users")]
        profiles = [dict(row) for row in connection.execute("SELECT * FROM profiles")]
    finally:
        connection.close()

    user_ids = set()
    emails = set()
    for row in users:
        row["user_id"] = valid_uuid(row["user_id"], "users.user_id")
        normalized = row["email"].strip().lower()
        if normalized in emails:
            raise ValueError("SQLite contains duplicate emails after lowercase normalization")
        emails.add(normalized)
        user_ids.add(row["user_id"])
        row["email"] = normalized
        row["created_at"] = parse_time(row["created_at"])

    for row in profiles:
        row["user_id"] = valid_uuid(row["user_id"], "profiles.user_id")
        if row["user_id"] not in user_ids:
            raise ValueError("SQLite contains an orphaned profile")
        # Accept both the original SQLite schema (sketch_md/taste_json) and the
        # SQLAlchemy compatibility schema (character_md/weights). Running the
        # newer app once may already have upgraded the local file in place.
        row["sketch_md"] = row.get("sketch_md") or row.get("character_md")
        if not row["sketch_md"]:
            raise ValueError("profiles row is missing character markdown")
        raw_weights = row.get("taste_json", row.get("weights"))
        row["weights"] = json.loads(raw_weights) if isinstance(raw_weights, str) else raw_weights
        if row["weights"] is not None and not isinstance(row["weights"], dict):
            raise ValueError("profile weights must contain a JSON object")
        row["profile_id"] = row.get("profile_id") or str(uuid.uuid5(
            PROFILE_NAMESPACE, f'{row["user_id"]}:{row["kind"]}:{row["slug"]}'
        ))
        row["updated_at"] = parse_time(row["updated_at"])
    return users, profiles


def main() -> int:
    load_dotenv()
    args = parse_args()
    raw_url = args.database_url or os.environ.get("DATABASE_URL", "")
    if not raw_url:
        raise SystemExit("DATABASE_URL or --database-url is required")
    users, profiles = load_source(args.sqlite_path)
    print(f"Validated {len(users)} users and {len(profiles)} profiles from SQLite.")
    if args.dry_run:
        return 0

    engine = create_engine(postgres_url(raw_url), pool_pre_ping=True)
    if engine.dialect.name != "postgresql":
        raise SystemExit("Migration target must be PostgreSQL")
    if not inspect(engine).has_table("alembic_version"):
        raise SystemExit("PostgreSQL schema is not migrated; run `alembic upgrade head` first")

    imported_user_ids = [row["user_id"] for row in users]
    with engine.begin() as connection:
        for row in users:
            statement = insert(User).values(
                user_id=row["user_id"],
                email=row["email"],
                pw_salt=row["pw_salt"],
                pw_hash=row["pw_hash"],
                password_scheme="pbkdf2_sha256",
                created_at=row["created_at"],
                updated_at=row["created_at"],
            ).on_conflict_do_nothing(index_elements=["user_id"])
            connection.execute(statement)

        for row in profiles:
            statement = insert(Profile).values(
                profile_id=row["profile_id"],
                user_id=row["user_id"],
                kind=row["kind"],
                slug=row["slug"],
                name=row["name"],
                character_md=row["sketch_md"],
                weights=row["weights"],
                weights_schema_version=1,
                version=1,
                created_at=row["updated_at"],
                updated_at=row["updated_at"],
            ).on_conflict_do_update(
                constraint="uq_profiles_owner_kind_slug",
                set_={
                    "name": row["name"],
                    "character_md": row["sketch_md"],
                    "weights": row["weights"],
                    "updated_at": row["updated_at"],
                },
            )
            connection.execute(statement)

        imported_users = connection.scalar(select(func.count()).select_from(User).where(
            User.user_id.in_(imported_user_ids)
        )) if imported_user_ids else 0
        imported_profiles = connection.scalar(select(func.count()).select_from(Profile).where(
            Profile.user_id.in_(imported_user_ids)
        )) if imported_user_ids else 0
        if imported_users != len(users) or imported_profiles < len(profiles):
            raise RuntimeError("PostgreSQL verification counts did not match the SQLite source")

    print(f"Imported {len(users)} users and {len(profiles)} profiles transactionally.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
