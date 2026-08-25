"""SQLAlchemy models for CheckIn's durable application state.

PostgreSQL gets native UUID and JSONB columns. SQLite uses portable variants so
the existing fast unit tests can continue to create isolated temporary stores.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_uuid() -> str:
    return str(uuid.uuid4())


UUID_TYPE = UUID(as_uuid=False).with_variant(String(36), "sqlite")
JSON_TYPE = JSONB().with_variant(JSON(), "sqlite")


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        Index("uq_users_email_lower", text("lower(email)"), unique=True),
    )

    user_id: Mapped[str] = mapped_column(UUID_TYPE, primary_key=True, default=new_uuid)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    pw_salt: Mapped[str] = mapped_column(String(128), nullable=False)
    pw_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    password_scheme: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pbkdf2_sha256", server_default="pbkdf2_sha256"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class SessionRecord(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        Index("ix_sessions_user_expires", "user_id", "expires_at"),
    )

    session_id: Mapped[str] = mapped_column(UUID_TYPE, primary_key=True, default=new_uuid)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    user_id: Mapped[str] = mapped_column(
        UUID_TYPE, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Profile(Base):
    __tablename__ = "profiles"
    __table_args__ = (
        UniqueConstraint("user_id", "kind", "slug", name="uq_profiles_owner_kind_slug"),
        CheckConstraint("kind IN ('self', 'cotraveller')", name="ck_profiles_kind"),
        Index("ix_profiles_user_kind", "user_id", "kind"),
    )

    profile_id: Mapped[str] = mapped_column(UUID_TYPE, primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(
        UUID_TYPE, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    character_md: Mapped[str] = mapped_column(Text, nullable=False)
    weights: Mapped[dict[str, Any] | None] = mapped_column(JSON_TYPE)
    weights_schema_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class ProfileIntake(Base):
    __tablename__ = "profile_intakes"
    __table_args__ = (
        UniqueConstraint("user_id", "kind", "slug", name="uq_profile_intakes_owner_kind_slug"),
        CheckConstraint("kind IN ('self', 'cotraveller')", name="ck_profile_intakes_kind"),
        CheckConstraint(
            "status IN ('in_progress', 'ready_to_complete', 'completing', "
            "'completion_failed', 'completed', 'abandoned')",
            name="ck_profile_intakes_status",
        ),
    )

    intake_id: Mapped[str] = mapped_column(UUID_TYPE, primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(
        UUID_TYPE, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(24), nullable=False, default="self")
    slug: Mapped[str] = mapped_column(String(120), nullable=False, default="self")
    transcript: Mapped[list[dict[str, Any]]] = mapped_column(JSON_TYPE, nullable=False, default=list)
    answers: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    current_question: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="in_progress")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class Trip(Base):
    __tablename__ = "trips"
    __table_args__ = (
        Index("ix_trips_user_created", "user_id", "created_at"),
    )

    trip_id: Mapped[str] = mapped_column(UUID_TYPE, primary_key=True)
    user_id: Mapped[str] = mapped_column(
        UUID_TYPE, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    state_json: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, nullable=False)
    profile_version: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class TripFeedback(Base):
    __tablename__ = "trip_feedback"
    __table_args__ = (
        UniqueConstraint("trip_id", "user_id", name="uq_trip_feedback_trip_user"),
        UniqueConstraint("user_id", "idempotency_key", name="uq_trip_feedback_idempotency"),
        CheckConstraint("rating BETWEEN 1 AND 5", name="ck_trip_feedback_rating"),
        Index("ix_trip_feedback_user_created", "user_id", "created_at"),
    )

    feedback_id: Mapped[str] = mapped_column(UUID_TYPE, primary_key=True, default=new_uuid)
    trip_id: Mapped[str] = mapped_column(
        UUID_TYPE, ForeignKey("trips.trip_id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        UUID_TYPE, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    comments: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class PreferenceEvent(Base):
    __tablename__ = "preference_events"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_preference_events_idempotency"),
        CheckConstraint(
            "event_type IN ('selection', 'post_trip_rating', "
            "'explicit_like', 'explicit_dislike')",
            name="ck_preference_events_type",
        ),
        Index("ix_preference_events_profile_created", "profile_id", "created_at"),
        Index("ix_preference_events_trip", "trip_id"),
    )

    event_id: Mapped[str] = mapped_column(UUID_TYPE, primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(
        UUID_TYPE, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    profile_id: Mapped[str] = mapped_column(
        UUID_TYPE, ForeignKey("profiles.profile_id", ondelete="CASCADE"), nullable=False
    )
    trip_id: Mapped[str | None] = mapped_column(
        UUID_TYPE, ForeignKey("trips.trip_id", ondelete="CASCADE")
    )
    feedback_id: Mapped[str | None] = mapped_column(
        UUID_TYPE, ForeignKey("trip_feedback.feedback_id", ondelete="SET NULL")
    )
    recommendation_id: Mapped[str | None] = mapped_column(String(128))
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    signal_value: Mapped[float] = mapped_column(nullable=False, default=0.0)
    vibe_tags: Mapped[list[str]] = mapped_column(JSON_TYPE, nullable=False, default=list)
    weight_delta: Mapped[dict[str, float]] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    profile_version_before: Mapped[int] = mapped_column(Integer, nullable=False)
    profile_version_after: Mapped[int] = mapped_column(Integer, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
