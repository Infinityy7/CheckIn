"""Durable persistence backed by PostgreSQL, with SQLite test compatibility.

The public functions intentionally remain synchronous so current callers can
migrate incrementally. PostgreSQL connections come from SQLAlchemy's bounded,
health-checked pool; SQLite is retained only as a local/test fallback.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv
from sqlalchemy import create_engine, delete, func, inspect, select
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError as SAIntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from .database_models import (
    Base,
    CompanionLink,
    LLMResearchCache,
    PreferenceEvent,
    Profile,
    ProfileIntake,
    SessionRecord,
    Trip,
    TripFeedback,
    User,
    utc_now,
)

load_dotenv()
logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent / "travelbuddy.db"

# Compatibility hook: existing tests set ``db._conn = None`` after changing
# DB_PATH. The value is now an Engine rather than a raw sqlite connection.
_conn: Engine | None = None
_engine_url: str | None = None


class ProfileVersionConflict(RuntimeError):
    """Raised when a learner tries to overwrite a newer profile version."""


class TripIdempotencyConflict(RuntimeError):
    """Raised when a trip insert collides with an existing (user, idempotency key)."""


def _normalize_database_url(url: str) -> str:
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def _database_url() -> str:
    configured = os.environ.get("DATABASE_URL", "").strip()
    if configured:
        return _normalize_database_url(configured)
    if os.environ.get("APP_ENV", "development").strip().lower() == "production":
        raise RuntimeError(
            "DATABASE_URL is not set and APP_ENV=production forbids the SQLite fallback. "
            "Point DATABASE_URL at the PostgreSQL database before starting the API."
        )
    return f"sqlite+pysqlite:///{DB_PATH}"


def _positive_int(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def _connect() -> Engine:
    """Return the process-local SQLAlchemy Engine/pool."""
    global _conn, _engine_url
    url = _database_url()
    if _conn is not None and _engine_url == url:
        return _conn
    if _conn is not None:
        _conn.dispose()

    if url.startswith("sqlite"):
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = create_engine(
            url,
            connect_args={"check_same_thread": False},
            poolclass=NullPool,
        )
    else:
        _conn = create_engine(
            url,
            pool_pre_ping=True,
            pool_size=_positive_int("DB_POOL_SIZE", 5),
            max_overflow=_positive_int("DB_MAX_OVERFLOW", 10),
            pool_timeout=_positive_int("DB_POOL_TIMEOUT_SECONDS", 30),
            pool_recycle=_positive_int("DB_POOL_RECYCLE_SECONDS", 1800),
        )
    _engine_url = url
    return _conn


def dispose_engine() -> None:
    """Dispose pooled connections, primarily for tests and graceful shutdown."""
    global _conn, _engine_url
    if _conn is not None:
        _conn.dispose()
    _conn = None
    _engine_url = None


def init_db() -> None:
    """Initialize SQLite tests or verify an Alembic-migrated PostgreSQL schema."""
    engine = _connect()
    if engine.dialect.name == "sqlite":
        legacy_profiles = _prepare_legacy_sqlite_schema(engine)
        Base.metadata.create_all(engine)
        _finish_legacy_sqlite_schema(engine, legacy_profiles)
        _migrate_legacy_files()
        return

    # Production schemas are changed only through Alembic. Running create_all
    # in every web worker would race and leave Alembic without revision state.
    if not inspect(engine).has_table("alembic_version") or not inspect(engine).has_table("users"):
        raise RuntimeError(
            "PostgreSQL schema is not migrated. Run `alembic upgrade head` before starting the API."
        )
    _verify_alembic_head(engine)


def _recorded_revision(engine: Engine) -> str | None:
    with engine.connect() as connection:
        return connection.execute(
            sql_text("SELECT version_num FROM alembic_version")
        ).scalar_one_or_none()


def _script_head() -> str | None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    app_root = Path(__file__).resolve().parent.parent
    config = Config(str(app_root / "alembic.ini"))
    config.set_main_option("script_location", str(app_root / "db" / "migrations"))
    return ScriptDirectory.from_config(config).get_current_head()


def _verify_alembic_head(engine: Engine) -> None:
    """Refuse to start a worker whose code expects a different schema revision."""
    recorded = _recorded_revision(engine)
    head = _script_head()
    if recorded != head:
        raise RuntimeError(
            f"PostgreSQL schema is at Alembic revision {recorded or 'none'} but this build "
            f"expects {head or 'none'}. Run `alembic upgrade head` (or deploy the matching "
            "code) before starting the API."
        )


def _prepare_legacy_sqlite_schema(engine: Engine) -> list[dict[str, Any]]:
    """Make the original two-table SQLite DB compatible without losing data."""
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    legacy_profiles: list[dict[str, Any]] = []
    with engine.begin() as connection:
        if "users" in tables:
            user_columns = {column["name"] for column in inspector.get_columns("users")}
            if "password_scheme" not in user_columns:
                connection.exec_driver_sql(
                    "ALTER TABLE users ADD COLUMN password_scheme TEXT NOT NULL DEFAULT 'pbkdf2_sha256'"
                )
            if "updated_at" not in user_columns:
                connection.exec_driver_sql("ALTER TABLE users ADD COLUMN updated_at TEXT")
                connection.exec_driver_sql("UPDATE users SET updated_at = created_at WHERE updated_at IS NULL")
            for column, ddl in (
                ("username", "ALTER TABLE users ADD COLUMN username VARCHAR(40)"),
                ("name", "ALTER TABLE users ADD COLUMN name VARCHAR(160)"),
                ("phone", "ALTER TABLE users ADD COLUMN phone VARCHAR(32)"),
            ):
                if column not in user_columns:
                    connection.exec_driver_sql(ddl)
            _backfill_usernames(connection)

        if "trips" in tables:
            trip_columns = {column["name"] for column in inspector.get_columns("trips")}
            if "idempotency_key" not in trip_columns:
                connection.exec_driver_sql("ALTER TABLE trips ADD COLUMN idempotency_key VARCHAR(160)")

        if "profiles" in tables:
            profile_columns = {column["name"] for column in inspector.get_columns("profiles")}
            if "character_md" not in profile_columns:
                legacy_profiles = [
                    dict(row) for row in connection.exec_driver_sql("SELECT * FROM profiles").mappings()
                ]
                connection.exec_driver_sql("ALTER TABLE profiles RENAME TO profiles_legacy_sqlalchemy")
        if "profiles_legacy_sqlalchemy" in tables and not legacy_profiles:
            legacy_profiles = [
                dict(row) for row in connection.exec_driver_sql(
                    "SELECT * FROM profiles_legacy_sqlalchemy"
                ).mappings()
            ]
    return legacy_profiles


def normalize_username(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9_-]+", "-", value.strip().lower().lstrip("@")).strip("-_")
    return cleaned[:30]


def _backfill_usernames(connection) -> None:
    rows = connection.exec_driver_sql(
        "SELECT user_id, email FROM users WHERE username IS NULL OR username = ''"
    ).fetchall()
    if not rows:
        return
    taken = {
        row[0].lower()
        for row in connection.exec_driver_sql(
            "SELECT username FROM users WHERE username IS NOT NULL AND username != ''"
        ).fetchall()
    }
    for user_id, email in rows:
        base = normalize_username(str(email).split("@", 1)[0]) or "traveler"
        candidate = base
        suffix = 2
        while candidate in taken:
            candidate = f"{base}-{suffix}"
            suffix += 1
        taken.add(candidate)
        connection.exec_driver_sql(
            "UPDATE users SET username = ? WHERE user_id = ?"
            if connection.dialect.name == "sqlite"
            else "UPDATE users SET username = %s WHERE user_id = %s",
            (candidate, user_id),
        )


def _finish_legacy_sqlite_schema(engine: Engine, legacy_profiles: list[dict[str, Any]]) -> None:
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_email_lower ON users (lower(email))"
        )
        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_username_lower ON users (lower(username))"
        )
        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_trips_user_idempotency ON trips (user_id, idempotency_key)"
        )
        for row in legacy_profiles:
            weights = _json_object(row.get("taste_json")) if row.get("taste_json") else None
            profile_id = str(uuid.uuid5(
                uuid.NAMESPACE_URL,
                f'travelbuddy:{row["user_id"]}:{row["kind"]}:{row["slug"]}',
            ))
            connection.execute(sqlite_insert(Profile).values(
                profile_id=profile_id,
                user_id=row["user_id"],
                kind=row["kind"],
                slug=row["slug"],
                name=row["name"],
                character_md=row["sketch_md"],
                weights=weights,
                weights_schema_version=1,
                version=1,
                created_at=_parse_datetime(row.get("updated_at"), default_now=True),
                updated_at=_parse_datetime(row.get("updated_at"), default_now=True),
            ).on_conflict_do_nothing(index_elements=["user_id", "kind", "slug"]))
        if inspect(engine).has_table("profiles_legacy_sqlalchemy"):
            connection.exec_driver_sql("DROP TABLE profiles_legacy_sqlalchemy")


def _parse_datetime(value: str | datetime | None, *, default_now: bool = False) -> datetime | None:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str) and value:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    elif default_now:
        result = utc_now()
    else:
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result


def _iso(value: datetime | str | None) -> str | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return value


def _json_object(value: str | dict[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("profile weights must be a JSON object")
    return parsed


def _portable_json(value: Any) -> Any:
    """Convert dates/Pydantic values into JSON-compatible primitives."""
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.loads(json.dumps(value, default=lambda item: _iso(item) or str(item)))


def _insert_for(engine: Engine, model):
    return sqlite_insert(model) if engine.dialect.name == "sqlite" else postgres_insert(model)


def _user_dict(row: User) -> dict[str, Any]:
    return {
        "user_id": row.user_id,
        "email": row.email,
        "username": row.username,
        "name": row.name,
        "phone": row.phone,
        "pw_salt": row.pw_salt,
        "pw_hash": row.pw_hash,
        "password_scheme": row.password_scheme,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _profile_dict(row: Profile) -> dict[str, Any]:
    return {
        "id": row.profile_id,
        "profile_id": row.profile_id,
        "user_id": row.user_id,
        "kind": row.kind,
        "slug": row.slug,
        "name": row.name,
        # Legacy aliases keep profiles.py unchanged during the DB rollout.
        "sketch_md": row.character_md,
        "character_md": row.character_md,
        "taste_json": json.dumps(row.weights) if row.weights is not None else None,
        "weights": row.weights,
        "weights_schema_version": row.weights_schema_version,
        "version": row.version,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def create_user(
    user_id: str,
    email: str,
    pw_salt: str,
    pw_hash: str,
    created_at: str,
    *,
    username: str | None = None,
    name: str | None = None,
    phone: str | None = None,
) -> None:
    """Insert a user; duplicate identifiers retain the legacy exception contract."""
    try:
        with Session(_connect()) as session, session.begin():
            session.add(User(
                user_id=user_id,
                email=email.strip().lower(),
                username=username.strip().lower() if username else None,
                name=name,
                phone=phone,
                pw_salt=pw_salt,
                pw_hash=pw_hash,
                created_at=_parse_datetime(created_at, default_now=True),
                updated_at=utc_now(),
            ))
    except SAIntegrityError as exc:
        # auth.py and existing tests currently catch sqlite3.IntegrityError.
        raise sqlite3.IntegrityError("User ID, email, or username already registered") from exc


def create_user_with_session(
    user_id: str,
    email: str,
    pw_salt: str,
    pw_hash: str,
    created_at: str,
    token_hash: str,
    expires_at_iso: str | datetime,
    *,
    username: str | None = None,
    name: str | None = None,
    phone: str | None = None,
) -> None:
    """Create an account and its first hashed session in one transaction."""
    expires_at = _parse_datetime(expires_at_iso)
    if expires_at is None:
        raise ValueError("expires_at is required")
    try:
        with Session(_connect()) as session, session.begin():
            session.add(User(
                user_id=user_id,
                email=email.strip().lower(),
                username=username.strip().lower() if username else None,
                name=name,
                phone=phone,
                pw_salt=pw_salt,
                pw_hash=pw_hash,
                created_at=_parse_datetime(created_at, default_now=True),
                updated_at=utc_now(),
            ))
            # Flush the parent first so this stays reliable without ORM
            # relationships and rolls the user back if the session insert fails.
            session.flush()
            session.add(SessionRecord(
                token_hash=token_hash,
                user_id=user_id,
                expires_at=expires_at,
            ))
            session.flush()
    except SAIntegrityError as exc:
        raise sqlite3.IntegrityError("User/email or session token already exists") from exc


def get_user_by_email(email: str) -> dict | None:
    with Session(_connect()) as session:
        row = session.scalar(select(User).where(User.email == email.strip().lower()))
        return _user_dict(row) if row else None


def get_user_by_username(username: str) -> dict | None:
    candidate = username.strip().lower().lstrip("@")
    if not candidate:
        return None
    with Session(_connect()) as session:
        row = session.scalar(select(User).where(func.lower(User.username) == candidate))
        return _user_dict(row) if row else None


def get_user_by_id(user_id: str) -> dict | None:
    with Session(_connect()) as session:
        row = session.get(User, user_id)
        return _user_dict(row) if row else None


def all_users() -> list[dict]:
    with Session(_connect()) as session:
        return [_user_dict(row) for row in session.scalars(select(User).order_by(User.created_at))]


def save_profile(
    user_id: str,
    kind: str,
    slug: str,
    name: str,
    sketch_md: str,
    taste_json: str | dict | None,
) -> None:
    """Upsert character.md plus its structured weights JSON."""
    engine = _connect()
    now = utc_now()
    values = {
        "user_id": user_id,
        "kind": kind,
        "slug": slug,
        "name": name,
        "character_md": sketch_md,
        "weights": _json_object(taste_json),
        "updated_at": now,
    }
    statement = _insert_for(engine, Profile).values(**values)
    statement = statement.on_conflict_do_update(
        index_elements=["user_id", "kind", "slug"],
        set_={
            "name": name,
            "character_md": sketch_md,
            "weights": values["weights"],
            "version": Profile.version + 1,
            "updated_at": now,
        },
    )
    with engine.begin() as connection:
        connection.execute(statement)


def update_profile(
    user_id: str,
    character_md: str,
    weights: dict[str, Any],
    expected_version: int | None = None,
) -> dict:
    """Atomically replace the self profile with optimistic version checking."""
    with Session(_connect()) as session, session.begin():
        profile = session.scalar(select(Profile).where(
            Profile.user_id == user_id,
            Profile.kind == "self",
            Profile.slug == "self",
        ).with_for_update())
        if profile is None:
            raise ValueError("profile not found")
        if expected_version is not None and profile.version != expected_version:
            raise ProfileVersionConflict(
                f"profile version is {profile.version}, expected {expected_version}"
            )
        profile.character_md = character_md
        profile.weights = _portable_json(weights)
        profile.version += 1
        profile.updated_at = utc_now()
        session.flush()
        result = _profile_dict(profile)
    return result


def get_profile(user_id: str, kind: str, slug: str = "self") -> dict | None:
    with Session(_connect()) as session:
        row = session.scalar(select(Profile).where(
            Profile.user_id == user_id,
            Profile.kind == kind,
            Profile.slug == slug,
        ))
        return _profile_dict(row) if row else None


def delete_profile(user_id: str, kind: str, slug: str = "self") -> bool:
    with Session(_connect()) as session, session.begin():
        result = session.execute(delete(Profile).where(
            Profile.user_id == user_id,
            Profile.kind == kind,
            Profile.slug == slug,
        ))
        return bool(result.rowcount)


def list_cotraveller_profiles(user_id: str) -> list[dict]:
    with Session(_connect()) as session:
        rows = session.scalars(select(Profile).where(
            Profile.user_id == user_id,
            Profile.kind == "cotraveller",
        ).order_by(Profile.slug))
        return [_profile_dict(row) for row in rows]


# --- durable authentication sessions ---

def save_session(token_hash: str, user_id: str, expires_at_iso: str | datetime) -> None:
    expires_at = _parse_datetime(expires_at_iso)
    if expires_at is None:
        raise ValueError("expires_at is required")
    engine = _connect()
    statement = _insert_for(engine, SessionRecord).values(
        token_hash=token_hash,
        user_id=user_id,
        expires_at=expires_at,
        revoked_at=None,
    )
    statement = statement.on_conflict_do_update(
        index_elements=["token_hash"],
        set_={"user_id": user_id, "expires_at": expires_at, "revoked_at": None},
    )
    with engine.begin() as connection:
        connection.execute(statement)


create_session = save_session


def get_session(token_hash: str) -> dict | None:
    with Session(_connect()) as session:
        row = session.scalar(select(SessionRecord).where(SessionRecord.token_hash == token_hash))
        if row is None:
            return None
        return {
            "session_id": row.session_id,
            "token_hash": row.token_hash,
            "user_id": row.user_id,
            "created_at": _iso(row.created_at),
            "expires_at": _iso(row.expires_at),
            "revoked_at": _iso(row.revoked_at),
        }


def get_session_user(token_hash: str) -> str | None:
    now = utc_now()
    with Session(_connect()) as session:
        row = session.scalar(select(SessionRecord).where(
            SessionRecord.token_hash == token_hash,
            SessionRecord.revoked_at.is_(None),
            SessionRecord.expires_at > now,
        ))
        return row.user_id if row else None


def delete_session(token_hash: str) -> None:
    with Session(_connect()) as session, session.begin():
        session.execute(delete(SessionRecord).where(SessionRecord.token_hash == token_hash))


# --- resumable questionnaire intake ---

def _intake_dict(row: ProfileIntake) -> dict[str, Any]:
    return {
        "intake_id": row.intake_id,
        "user_id": row.user_id,
        "kind": row.kind,
        "slug": row.slug,
        "transcript": row.transcript,
        "answers": row.answers,
        "current_question": row.current_question,
        "status": row.status,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def save_profile_intake(
    user_id: str,
    state: dict[str, Any],
    kind: str = "self",
    slug: str = "self",
) -> dict:
    engine = _connect()
    now = utc_now()
    values = {
        "user_id": user_id,
        "kind": kind,
        "slug": slug,
        "transcript": _portable_json(state.get("transcript", [])),
        "answers": _portable_json(state.get("answers", {})),
        "current_question": int(state.get("current_question", len(state.get("answers", {})))),
        "status": state.get("status", "in_progress"),
        "updated_at": now,
    }
    statement = _insert_for(engine, ProfileIntake).values(**values)
    statement = statement.on_conflict_do_update(
        index_elements=["user_id", "kind", "slug"],
        set_={key: value for key, value in values.items() if key not in {"user_id", "kind", "slug"}},
    )
    with engine.begin() as connection:
        connection.execute(statement)
    return get_profile_intake(user_id, kind, slug) or {}


save_intake_state = save_profile_intake


def get_profile_intake(user_id: str, kind: str = "self", slug: str = "self") -> dict | None:
    with Session(_connect()) as session:
        row = session.scalar(select(ProfileIntake).where(
            ProfileIntake.user_id == user_id,
            ProfileIntake.kind == kind,
            ProfileIntake.slug == slug,
        ))
        return _intake_dict(row) if row else None


get_intake_state = get_profile_intake


def save_intake_answer(
    user_id: str,
    question_id: str,
    answer: Any,
    *,
    transcript: list[dict[str, Any]] | None = None,
    kind: str = "self",
    slug: str = "self",
) -> dict:
    """Append one controlled answer while optionally replacing the transcript."""
    current = get_profile_intake(user_id, kind, slug) or {
        "answers": {}, "transcript": [], "status": "in_progress"
    }
    answers = {**current.get("answers", {}), question_id: _portable_json(answer)}
    state = {
        "answers": answers,
        "transcript": transcript if transcript is not None else current.get("transcript", []),
        "current_question": len(answers),
        "status": "in_progress",
    }
    return save_profile_intake(user_id, state, kind, slug)


def delete_profile_intake(user_id: str, kind: str = "self", slug: str = "self") -> bool:
    with Session(_connect()) as session, session.begin():
        result = session.execute(delete(ProfileIntake).where(
            ProfileIntake.user_id == user_id,
            ProfileIntake.kind == kind,
            ProfileIntake.slug == slug,
        ))
        return bool(result.rowcount)


reset_intake = delete_profile_intake


# --- durable trip state ---

def save_trip_state(state_dict: dict[str, Any]) -> dict[str, Any]:
    state = _portable_json(state_dict)
    trip_id = state.get("trip_id")
    user_id = state.get("user_id")
    if not trip_id or not user_id:
        raise ValueError("trip state requires trip_id and user_id")
    engine = _connect()
    now = utc_now()
    idempotency_key = state.get("idempotency_key") or None
    values = {
        "trip_id": trip_id,
        "user_id": user_id,
        "state_json": state,
        "profile_version": state.get("profile_version"),
        "idempotency_key": idempotency_key,
        "created_at": _parse_datetime(state.get("created_at"), default_now=True),
        "updated_at": now,
    }
    statement = _insert_for(engine, Trip).values(**values)
    statement = statement.on_conflict_do_update(
        index_elements=["trip_id"],
        set_={
            "state_json": state,
            "profile_version": state.get("profile_version"),
            "updated_at": now,
        },
        # A UUID collision or buggy caller must never rewrite another user's
        # JSON owner while leaving the indexed relational owner unchanged.
        where=Trip.user_id == user_id,
    ).returning(Trip.user_id)
    try:
        with engine.begin() as connection:
            persisted_owner = connection.execute(statement).scalar_one_or_none()
            if persisted_owner != user_id:
                raise ValueError("trip owner does not match the persisted trip")
    except SAIntegrityError as exc:
        # ON CONFLICT only covers trip_id; a second trip under the same
        # (user_id, idempotency_key) trips the unique index instead.
        if idempotency_key is None:
            raise
        raise TripIdempotencyConflict(
            "a trip already exists for this user and idempotency key"
        ) from exc
    return state


def load_trip_state(trip_id: str) -> dict[str, Any] | None:
    with Session(_connect()) as session:
        row = session.get(Trip, trip_id)
        return _portable_json(row.state_json) if row else None


get_trip_state = load_trip_state


def find_trip_by_idempotency_key(user_id: str, idempotency_key: str) -> dict[str, Any] | None:
    with Session(_connect()) as session:
        row = session.scalar(select(Trip).where(
            Trip.user_id == user_id,
            Trip.idempotency_key == idempotency_key,
        ))
        return _portable_json(row.state_json) if row else None


def mutate_trip_state(
    trip_id: str,
    mutator: Callable[[dict[str, Any]], dict[str, Any] | None],
) -> dict[str, Any]:
    """Lock, mutate, validate, and persist one trip snapshot atomically.

    The callback may mutate its input in place and return ``None``, or return a
    replacement dict. Trip identity and ownership are immutable.
    """
    with Session(_connect()) as session, session.begin():
        row = session.scalar(select(Trip).where(Trip.trip_id == trip_id).with_for_update())
        if row is None:
            raise KeyError(f"Unknown trip {trip_id}")
        before = _portable_json(row.state_json)
        candidate = mutator(before)
        updated = before if candidate is None else _portable_json(candidate)
        if not isinstance(updated, dict):
            raise ValueError("trip mutator must return a dict or None")
        if updated.get("trip_id") != row.trip_id:
            raise ValueError("trip mutator cannot change trip_id")
        if updated.get("user_id") != row.user_id:
            raise ValueError("trip mutator cannot change trip owner")
        row.state_json = updated
        row.updated_at = utc_now()
        session.flush()
        result = _portable_json(row.state_json)
    return result


def list_trip_states(user_id: str) -> list[dict[str, Any]]:
    with Session(_connect()) as session:
        rows = session.scalars(select(Trip).where(Trip.user_id == user_id).order_by(Trip.created_at.desc()))
        return [_portable_json(row.state_json) for row in rows]


list_user_trip_states = list_trip_states


# --- post-trip feedback and auditable learning events ---

def _feedback_dict(row: TripFeedback) -> dict[str, Any]:
    return {
        "feedback_id": row.feedback_id,
        "trip_id": row.trip_id,
        "user_id": row.user_id,
        "rating": row.rating,
        "comments": row.comments,
        "details": row.details,
        "idempotency_key": row.idempotency_key,
        "created_at": _iso(row.created_at),
    }


def create_trip_feedback(
    trip_id: str,
    user_id: str,
    rating: int,
    comments: str | None = None,
    details: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> dict:
    if rating < 1 or rating > 5:
        raise ValueError("rating must be between 1 and 5")
    key = idempotency_key or f"trip-feedback:{trip_id}"
    engine = _connect()
    statement = _insert_for(engine, TripFeedback).values(
        trip_id=trip_id,
        user_id=user_id,
        rating=rating,
        comments=comments,
        details=_portable_json(details or {}),
        idempotency_key=key,
    ).on_conflict_do_nothing()
    with engine.begin() as connection:
        connection.execute(statement)
    existing = get_trip_feedback(trip_id, user_id)
    if existing is None:
        raise RuntimeError("Trip feedback could not be persisted")
    return existing


def get_trip_feedback(trip_id: str, user_id: str) -> dict | None:
    with Session(_connect()) as session:
        row = session.scalar(select(TripFeedback).where(
            TripFeedback.trip_id == trip_id,
            TripFeedback.user_id == user_id,
        ))
        return _feedback_dict(row) if row else None


def _event_dict(row: PreferenceEvent) -> dict[str, Any]:
    return {
        "event_id": row.event_id,
        "user_id": row.user_id,
        "profile_id": row.profile_id,
        "trip_id": row.trip_id,
        "feedback_id": row.feedback_id,
        "recommendation_id": row.recommendation_id,
        "event_type": row.event_type,
        "signal_value": row.signal_value,
        "vibe_tags": row.vibe_tags,
        "weight_delta": row.weight_delta,
        "payload": row.payload,
        "idempotency_key": row.idempotency_key,
        "profile_version_before": row.profile_version_before,
        "profile_version_after": row.profile_version_after,
        "occurred_at": _iso(row.occurred_at),
        "created_at": _iso(row.created_at),
    }


def update_profile_weights_with_events(
    user_id: str,
    new_weights: dict[str, Any],
    events: list[dict[str, Any]],
    *,
    kind: str = "self",
    slug: str = "self",
    expected_version: int | None = None,
) -> dict:
    """Atomically apply one learned weight snapshot and its immutable events.

    Event idempotency keys make retries safe. A fully replayed event batch is a
    no-op; a partially replayed batch is rejected because applying its supplied
    weight snapshot could double-count evidence.
    """
    if not events:
        raise ValueError("at least one preference event is required")
    keys = [str(event.get("idempotency_key", "")).strip() for event in events]
    if any(not key for key in keys) or len(keys) != len(set(keys)):
        raise ValueError("preference events require unique, non-empty idempotency_key values")

    with Session(_connect()) as session, session.begin():
        profile = session.scalar(select(Profile).where(
            Profile.user_id == user_id,
            Profile.kind == kind,
            Profile.slug == slug,
        ).with_for_update())
        if profile is None:
            raise ValueError("profile not found")

        existing = set(session.scalars(select(PreferenceEvent.idempotency_key).where(
            PreferenceEvent.user_id == user_id,
            PreferenceEvent.idempotency_key.in_(keys),
        )))
        if len(existing) == len(keys):
            return _profile_dict(profile)
        if existing:
            raise ValueError("preference event batch was only partially applied")
        if expected_version is not None and profile.version != expected_version:
            raise ProfileVersionConflict(
                f"profile version is {profile.version}, expected {expected_version}"
            )

        before = profile.version
        after = before + 1
        profile.weights = _portable_json(new_weights)
        profile.version = after
        profile.updated_at = utc_now()

        for event, key in zip(events, keys):
            session.add(PreferenceEvent(
                user_id=user_id,
                profile_id=profile.profile_id,
                trip_id=event.get("trip_id"),
                feedback_id=event.get("feedback_id"),
                recommendation_id=event.get("recommendation_id"),
                event_type=event["event_type"],
                signal_value=float(event.get("signal_value", 0.0)),
                vibe_tags=_portable_json(event.get("vibe_tags", [])),
                weight_delta=_portable_json(event.get("weight_delta", {})),
                payload=_portable_json(event.get("payload", {})),
                idempotency_key=key,
                profile_version_before=before,
                profile_version_after=after,
                occurred_at=_parse_datetime(event.get("occurred_at"), default_now=True),
            ))
        session.flush()
        result = _profile_dict(profile)
    return result


append_preference_events_and_update_profile = update_profile_weights_with_events


def _normalized_vibe_adjustment(
    weights: dict[str, Any] | None,
    adjustments: dict[str, float],
) -> dict[str, Any]:
    """Apply conservative deltas and keep the controlled vibe vector normalized."""
    # Keep the persistence transaction aligned with the one canonical learner.
    # The local import avoids coupling database module import to ranking setup.
    from personalization import apply_weight_adjustments

    return _portable_json(apply_weight_adjustments(weights or {}, adjustments))


def _actual_adjustment_rows(
    before: dict[str, Any],
    after: dict[str, Any],
    requested: dict[str, float],
) -> list[dict[str, Any]]:
    before_vibes = before.get("vibe_weights") or {}
    after_vibes = after.get("vibe_weights") or {}
    return [
        {
            "key": key,
            "before": round(float(before_vibes.get(key, 0.0)), 4),
            "after": round(float(after_vibes.get(key, 0.0)), 4),
            "delta": round(
                float(after_vibes.get(key, 0.0)) - float(before_vibes.get(key, 0.0)), 4
            ),
        }
        for key in requested
    ]


def apply_preference_learning(
    user_id: str,
    trip_id: str,
    event_type: str,
    idempotency_key: str,
    adjustments: dict[str, float],
    *,
    rating: int | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Atomically nudge self-profile vibes and record the evidence.

    A retry with the same user/idempotency key returns the previously recorded
    event and current profile without applying the delta twice. When ``rating``
    is supplied, the one-per-trip feedback row is created in the same commit.
    """
    allowed = {"selection", "post_trip_rating", "explicit_like", "explicit_dislike"}
    if event_type not in allowed:
        raise ValueError(f"unsupported preference event type: {event_type}")
    if not idempotency_key.strip():
        raise ValueError("idempotency_key is required")
    if rating is not None and not 1 <= rating <= 5:
        raise ValueError("rating must be between 1 and 5")

    with Session(_connect()) as session, session.begin():
        trip_owner = session.scalar(select(Trip.user_id).where(Trip.trip_id == trip_id))
        if trip_owner is None:
            raise ValueError("trip not found")
        if trip_owner != user_id:
            raise ValueError("trip does not belong to user")

        profile = session.scalar(select(Profile).where(
            Profile.user_id == user_id,
            Profile.kind == "self",
            Profile.slug == "self",
        ).with_for_update())
        if profile is None:
            raise ValueError("profile not found")

        previous = session.scalar(select(PreferenceEvent).where(
            PreferenceEvent.user_id == user_id,
            PreferenceEvent.idempotency_key == idempotency_key,
        ))
        if previous is not None:
            previous_feedback = (
                session.get(TripFeedback, previous.feedback_id) if previous.feedback_id else None
            )
            return {
                "applied": False,
                "profile": _profile_dict(profile),
                "event": _event_dict(previous),
                "feedback": _feedback_dict(previous_feedback) if previous_feedback else None,
                "adjustments": (previous.payload or {}).get("adjustments", []),
            }

        before_weights = _portable_json(profile.weights or {})
        after_weights = _normalized_vibe_adjustment(before_weights, adjustments)
        actual_adjustments = _actual_adjustment_rows(before_weights, after_weights, adjustments)
        event_payload = _portable_json(payload or {})
        # Request-side projections are informational only. The authoritative
        # audit rows come from the locked before/after snapshots here.
        event_payload["adjustments"] = actual_adjustments

        feedback = None
        if rating is not None:
            feedback = session.scalar(select(TripFeedback).where(
                TripFeedback.trip_id == trip_id,
                TripFeedback.user_id == user_id,
            ))
            if feedback is None:
                feedback = TripFeedback(
                    trip_id=trip_id,
                    user_id=user_id,
                    rating=rating,
                    comments=event_payload.get("comments"),
                    details=event_payload,
                    idempotency_key=f"{idempotency_key}:feedback",
                )
                session.add(feedback)
                session.flush()
            elif feedback.rating != rating:
                raise ValueError("trip feedback was already submitted with a different rating")
            else:
                feedback.comments = event_payload.get("comments", feedback.comments)
                feedback.details = event_payload

        before = profile.version
        profile.weights = after_weights
        profile.version = before + 1
        profile.updated_at = utc_now()
        event = PreferenceEvent(
            user_id=user_id,
            profile_id=profile.profile_id,
            trip_id=trip_id,
            feedback_id=feedback.feedback_id if feedback else None,
            recommendation_id=(payload or {}).get("recommendation_id"),
            event_type=event_type,
            signal_value=(rating - 3) / 2 if rating is not None else float((payload or {}).get("signal_value", 0.0)),
            vibe_tags=list(adjustments),
            weight_delta=_portable_json(adjustments),
            payload=event_payload,
            idempotency_key=idempotency_key,
            profile_version_before=before,
            profile_version_after=before + 1,
            occurred_at=_parse_datetime((payload or {}).get("occurred_at"), default_now=True),
        )
        session.add(event)
        session.flush()
        result = {
            "applied": True,
            "profile": _profile_dict(profile),
            "event": _event_dict(event),
            "feedback": _feedback_dict(feedback) if feedback else None,
            "adjustments": actual_adjustments,
        }
    return result


def list_preference_events(user_id: str, limit: int = 100) -> list[dict[str, Any]]:
    with Session(_connect()) as session:
        rows = session.scalars(select(PreferenceEvent).where(
            PreferenceEvent.user_id == user_id,
        ).order_by(PreferenceEvent.created_at.desc()).limit(max(1, min(limit, 1000))))
        return [_event_dict(row) for row in rows]


# --- companion links ---

COMPANION_LINK_STATUSES = ("pending", "accepted", "declined", "revoked")


def _companion_link_dict(row: CompanionLink) -> dict[str, Any]:
    return {
        "link_id": row.link_id,
        "inviter_user_id": row.inviter_user_id,
        "invitee_user_id": row.invitee_user_id,
        "status": row.status,
        "created_at": _iso(row.created_at),
        "responded_at": _iso(row.responded_at),
    }


def _companion_link_row(session: Session, inviter_id: str, invitee_id: str) -> CompanionLink | None:
    return session.scalar(select(CompanionLink).where(
        CompanionLink.inviter_user_id == inviter_id,
        CompanionLink.invitee_user_id == invitee_id,
    ))


def create_or_reset_companion_link(inviter_id: str, invitee_id: str) -> dict:
    """Invite ``invitee_id``. A declined or revoked link becomes pending again; pending/accepted stay as they are."""
    if inviter_id == invitee_id:
        raise ValueError("A traveler cannot invite themselves")
    engine = _connect()
    for _attempt in range(2):
        try:
            with Session(engine) as session, session.begin():
                row = _companion_link_row(session, inviter_id, invitee_id)
                if row is None:
                    row = CompanionLink(
                        inviter_user_id=inviter_id, invitee_user_id=invitee_id, status="pending"
                    )
                    session.add(row)
                elif row.status in ("declined", "revoked"):
                    row.status = "pending"
                    row.created_at = utc_now()
                    row.responded_at = None
                session.flush()
                return _companion_link_dict(row)
        except SAIntegrityError:
            continue
    raise RuntimeError("companion link could not be created")


def get_companion_link(inviter_id: str, invitee_id: str) -> dict | None:
    with Session(_connect()) as session:
        row = _companion_link_row(session, inviter_id, invitee_id)
        return _companion_link_dict(row) if row else None


def get_companion_link_by_id(link_id: str) -> dict | None:
    with Session(_connect()) as session:
        row = session.get(CompanionLink, link_id)
        return _companion_link_dict(row) if row else None


def companion_link_status(inviter_id: str, invitee_id: str) -> str:
    """The inviter→invitee status, or 'none' when no invitation exists."""
    link = get_companion_link(inviter_id, invitee_id)
    return link["status"] if link else "none"


def list_companion_links(user_id: str) -> dict[str, list[dict[str, Any]]]:
    """Both directions; each row names only the other traveler, never a profile."""
    def public(row: CompanionLink, counterpart: User) -> dict[str, Any]:
        return {
            "link_id": row.link_id,
            "username": counterpart.username,
            "name": counterpart.name,
            "status": row.status,
            "created_at": _iso(row.created_at),
            "responded_at": _iso(row.responded_at),
        }

    with Session(_connect()) as session:
        incoming = session.execute(
            select(CompanionLink, User)
            .join(User, User.user_id == CompanionLink.inviter_user_id)
            .where(CompanionLink.invitee_user_id == user_id)
            .order_by(CompanionLink.created_at.desc())
        ).all()
        outgoing = session.execute(
            select(CompanionLink, User)
            .join(User, User.user_id == CompanionLink.invitee_user_id)
            .where(CompanionLink.inviter_user_id == user_id)
            .order_by(CompanionLink.created_at.desc())
        ).all()
        return {
            "incoming": [public(row, user) for row, user in incoming],
            "outgoing": [public(row, user) for row, user in outgoing],
        }


class CompanionLinkNotPending(RuntimeError):
    """Accept needs a pending invitation; decline needs a pending or accepted one."""


def respond_companion_link(link_id: str, invitee_id: str, status: str) -> dict | None:
    """The invitee accepts or declines a pending invitation. None when missing or not addressed to them."""
    if status not in ("accepted", "declined"):
        raise ValueError("status must be 'accepted' or 'declined'")
    with Session(_connect()) as session, session.begin():
        row = session.get(CompanionLink, link_id)
        if row is None or row.invitee_user_id != invitee_id:
            return None
        allowed = ("pending",) if status == "accepted" else ("pending", "accepted")
        if row.status not in allowed:
            raise CompanionLinkNotPending(f"invitation is {row.status}; cannot mark it {status}")
        row.status = status
        row.responded_at = utc_now()
        session.flush()
        return _companion_link_dict(row)


def delete_companion_link(link_id: str, user_id: str) -> dict | None:
    """The inviter revokes, the invitee declines; the row stays so a re-invite is possible. None for outsiders."""
    with Session(_connect()) as session, session.begin():
        row = session.get(CompanionLink, link_id)
        if row is None:
            return None
        if row.inviter_user_id == user_id:
            row.status = "revoked"
        elif row.invitee_user_id == user_id:
            row.status = "declined"
        else:
            return None
        row.responded_at = utc_now()
        session.flush()
        return _companion_link_dict(row)


def research_cache_fetch(exact_key: str, limit: int = 50) -> list[dict[str, Any]]:
    with Session(_connect()) as session:
        rows = session.scalars(
            select(LLMResearchCache)
            .where(LLMResearchCache.exact_key == exact_key)
            .order_by(LLMResearchCache.created_at.desc())
            .limit(max(1, min(limit, 200)))
        )
        return [
            {
                "cache_id": row.cache_id,
                "exact_key": row.exact_key,
                "taste_vector": row.taste_vector,
                "request_facts": row.request_facts,
                "payload": row.payload,
                "model": row.model,
                "created_at": _iso(row.created_at),
            }
            for row in rows
        ]


def research_cache_put(
    exact_key: str,
    taste_vector: list[float],
    request_facts: dict[str, Any],
    payload: list[dict[str, Any]],
    model: str,
    expire_before: datetime | None = None,
) -> None:
    with Session(_connect()) as session, session.begin():
        if expire_before is not None:
            session.execute(delete(LLMResearchCache).where(
                LLMResearchCache.exact_key == exact_key,
                LLMResearchCache.created_at <= expire_before,
            ))
        session.add(LLMResearchCache(
            exact_key=exact_key,
            taste_vector=_portable_json(taste_vector),
            request_facts=_portable_json(request_facts),
            payload=_portable_json(payload),
            model=model,
        ))


def research_cache_prune(expire_before: datetime) -> int:
    with Session(_connect()) as session, session.begin():
        result = session.execute(delete(LLMResearchCache).where(
            LLMResearchCache.created_at <= expire_before,
        ))
        return int(result.rowcount or 0)


def research_cache_clear() -> None:
    with Session(_connect()) as session, session.begin():
        session.execute(delete(LLMResearchCache))


def _migrate_legacy_files() -> None:
    """One-time import of pre-database files into the SQLite compatibility DB."""
    data_dir = DB_PATH.parent
    try:
        users_file = data_dir / "users.json"
        if users_file.exists():
            raw = json.loads(users_file.read_text())
            for info in raw.values():
                try:
                    create_user(
                        info["user_id"], info["email"], info["pw_salt"],
                        info["pw_hash"], info["created_at"],
                    )
                except sqlite3.IntegrityError:
                    pass
            users_file.rename(data_dir / "users.json.migrated")

        users_dir = data_dir / "users"
        if not users_dir.exists():
            return
        for user_dir in users_dir.iterdir():
            if not user_dir.is_dir():
                continue
            user_id = user_dir.name
            character_file = user_dir / "character.md"
            if character_file.exists() and get_profile(user_id, "self", "self") is None:
                save_profile(user_id, "self", "self", "self", character_file.read_text(), None)

            cotravellers_dir = user_dir / "cotravellers"
            if not cotravellers_dir.exists():
                continue
            for md_file in cotravellers_dir.iterdir():
                if md_file.suffix != ".md":
                    continue
                if get_profile(user_id, "cotraveller", md_file.stem) is None:
                    save_profile(
                        user_id, "cotraveller", md_file.stem, md_file.stem,
                        md_file.read_text(), None,
                    )
    except Exception as exc:
        logger.warning("legacy data migration failed: %s", exc)
