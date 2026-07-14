"""Email + password auth. Prototype-grade: sessions live in memory,
so a server restart logs everyone out. Users persist in SQLite (db.py)."""

from __future__ import annotations

import hashlib
import logging
import secrets
import sqlite3
import uuid
from datetime import datetime, timezone

from fastapi import Header, HTTPException

import db

logger = logging.getLogger(__name__)

_sessions: dict[str, str] = {}  # token -> user_id


def _hash_password(password: str, salt_hex: str) -> str:
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt_hex), 100_000
    )
    return digest.hex()


def _make_session(user_id: str) -> str:
    token = secrets.token_hex(16)
    _sessions[token] = user_id
    return token


def register(email: str, password: str) -> str:
    """Create a user and return a session token."""
    email = email.strip().lower()
    salt = secrets.token_hex(16)
    user_id = str(uuid.uuid4())
    try:
        db.create_user(
            user_id, email, salt, _hash_password(password, salt),
            datetime.now(timezone.utc).isoformat(),
        )
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Email already registered")
    logger.info("New user registered: %s", email)
    return _make_session(user_id)


def login(email: str, password: str) -> str:
    """Check credentials and return a session token."""
    email = email.strip().lower()
    record = db.get_user_by_email(email)
    if record is None:
        raise HTTPException(status_code=401, detail="Wrong email or password")
    if _hash_password(password, record["pw_salt"]) != record["pw_hash"]:
        raise HTTPException(status_code=401, detail="Wrong email or password")
    return _make_session(record["user_id"])


def logout(token: str) -> None:
    _sessions.pop(token, None)


def get_email(user_id: str) -> str:
    record = db.get_user_by_id(user_id)
    if record is None:
        return ""
    return record["email"]


def get_current_user(authorization: str | None = Header(None)) -> str:
    """FastAPI dependency: turns the Authorization header into a user_id."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not logged in")
    token = authorization[len("Bearer "):]
    user_id = _sessions.get(token)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Session expired — log in again")
    return user_id


def get_token(authorization: str | None = Header(None)) -> str:
    """FastAPI dependency: just the raw token (for logout)."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not logged in")
    return authorization[len("Bearer "):]
