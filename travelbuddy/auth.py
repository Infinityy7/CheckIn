"""Email/password authentication with durable, hashed sessions."""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import Header, HTTPException

import db

logger = logging.getLogger(__name__)

SESSION_TTL_DAYS = 30


def _hash_password(password: str, salt_hex: str) -> str:
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt_hex), 100_000
    )
    return digest.hex()


def _make_session(user_id: str) -> str:
    # The bearer credential is shown once. Only its digest is stored so a
    # database read cannot be turned directly into an authenticated session.
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(days=SESSION_TTL_DAYS)
    db.save_session(_token_hash(token), user_id, expires_at.isoformat())
    return token


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def register(email: str, password: str) -> str:
    """Create a user and return a session token."""
    email = email.strip().lower()
    salt = secrets.token_hex(16)
    user_id = str(uuid.uuid4())
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=SESSION_TTL_DAYS)
    try:
        db.create_user_with_session(
            user_id, email, salt, _hash_password(password, salt),
            now.isoformat(), _token_hash(token), expires_at.isoformat(),
        )
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Email already registered")
    logger.info("New user registered: %s", email)
    return token


def login(email: str, password: str) -> str:
    """Check credentials and return a session token."""
    email = email.strip().lower()
    record = db.get_user_by_email(email)
    if record is None:
        raise HTTPException(status_code=401, detail="Wrong email or password")
    if not hmac.compare_digest(_hash_password(password, record["pw_salt"]), record["pw_hash"]):
        raise HTTPException(status_code=401, detail="Wrong email or password")
    return _make_session(record["user_id"])


def logout(token: str) -> None:
    db.delete_session(_token_hash(token))


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
    # The database is authoritative so logout/revocation works across workers.
    user_id = db.get_session_user(_token_hash(token))
    if user_id is None:
        raise HTTPException(status_code=401, detail="Session expired — log in again")
    return user_id


def get_token(authorization: str | None = Header(None)) -> str:
    """FastAPI dependency: just the raw token (for logout)."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not logged in")
    return authorization[len("Bearer "):]
