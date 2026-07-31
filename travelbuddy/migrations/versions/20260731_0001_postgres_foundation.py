"""Create durable users, profiles, sessions, intakes, trips, and learning ledger.

Revision ID: 20260731_0001
Revises: None
Create Date: 2026-07-31
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260731_0001"
down_revision = None
branch_labels = None
depends_on = None


def _types():
    if op.get_bind().dialect.name == "postgresql":
        return postgresql.UUID(as_uuid=False), postgresql.JSONB()
    return sa.String(36), sa.JSON()


def upgrade() -> None:
    uuid_type, json_type = _types()
    timestamp = sa.DateTime(timezone=True)

    op.create_table(
        "users",
        sa.Column("user_id", uuid_type, primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("pw_salt", sa.String(128), nullable=False),
        sa.Column("pw_hash", sa.String(512), nullable=False),
        sa.Column("password_scheme", sa.String(32), nullable=False, server_default="pbkdf2_sha256"),
        sa.Column("created_at", timestamp, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", timestamp, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("uq_users_email_lower", "users", [sa.text("lower(email)")], unique=True)

    op.create_table(
        "sessions",
        sa.Column("session_id", uuid_type, primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("user_id", uuid_type, sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", timestamp, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("expires_at", timestamp, nullable=False),
        sa.Column("revoked_at", timestamp),
    )
    op.create_index("ix_sessions_user_expires", "sessions", ["user_id", "expires_at"])

    op.create_table(
        "profiles",
        sa.Column("profile_id", uuid_type, primary_key=True),
        sa.Column("user_id", uuid_type, sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("slug", sa.String(120), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("character_md", sa.Text(), nullable=False),
        sa.Column("weights", json_type),
        sa.Column("weights_schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", timestamp, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", timestamp, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("kind IN ('self', 'cotraveller')", name="ck_profiles_kind"),
        sa.UniqueConstraint("user_id", "kind", "slug", name="uq_profiles_owner_kind_slug"),
    )
    op.create_index("ix_profiles_user_kind", "profiles", ["user_id", "kind"])

    op.create_table(
        "profile_intakes",
        sa.Column("intake_id", uuid_type, primary_key=True),
        sa.Column("user_id", uuid_type, sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False, server_default="self"),
        sa.Column("slug", sa.String(120), nullable=False, server_default="self"),
        sa.Column("transcript", json_type, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("answers", json_type, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("current_question", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(24), nullable=False, server_default="in_progress"),
        sa.Column("created_at", timestamp, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", timestamp, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("kind IN ('self', 'cotraveller')", name="ck_profile_intakes_kind"),
        sa.CheckConstraint(
            "status IN ('in_progress', 'ready_to_complete', 'completing', "
            "'completion_failed', 'completed', 'abandoned')",
            name="ck_profile_intakes_status",
        ),
        sa.UniqueConstraint("user_id", "kind", "slug", name="uq_profile_intakes_owner_kind_slug"),
    )

    op.create_table(
        "trips",
        sa.Column("trip_id", uuid_type, primary_key=True),
        sa.Column("user_id", uuid_type, sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
        sa.Column("state_json", json_type, nullable=False),
        sa.Column("profile_version", sa.Integer()),
        sa.Column("created_at", timestamp, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", timestamp, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_trips_user_created", "trips", ["user_id", "created_at"])

    op.create_table(
        "trip_feedback",
        sa.Column("feedback_id", uuid_type, primary_key=True),
        sa.Column("trip_id", uuid_type, sa.ForeignKey("trips.trip_id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", uuid_type, sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("comments", sa.Text()),
        sa.Column("details", json_type, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("idempotency_key", sa.String(160), nullable=False),
        sa.Column("created_at", timestamp, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("rating BETWEEN 1 AND 5", name="ck_trip_feedback_rating"),
        sa.UniqueConstraint("trip_id", "user_id", name="uq_trip_feedback_trip_user"),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_trip_feedback_idempotency"),
    )
    op.create_index("ix_trip_feedback_user_created", "trip_feedback", ["user_id", "created_at"])

    op.create_table(
        "preference_events",
        sa.Column("event_id", uuid_type, primary_key=True),
        sa.Column("user_id", uuid_type, sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
        sa.Column("profile_id", uuid_type, sa.ForeignKey("profiles.profile_id", ondelete="CASCADE"), nullable=False),
        sa.Column("trip_id", uuid_type, sa.ForeignKey("trips.trip_id", ondelete="CASCADE")),
        sa.Column("feedback_id", uuid_type, sa.ForeignKey("trip_feedback.feedback_id", ondelete="SET NULL")),
        sa.Column("recommendation_id", sa.String(128)),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("signal_value", sa.Float(), nullable=False, server_default="0"),
        sa.Column("vibe_tags", json_type, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("weight_delta", json_type, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("payload", json_type, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("idempotency_key", sa.String(160), nullable=False),
        sa.Column("profile_version_before", sa.Integer(), nullable=False),
        sa.Column("profile_version_after", sa.Integer(), nullable=False),
        sa.Column("occurred_at", timestamp, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("created_at", timestamp, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint(
            "event_type IN ('selection', 'post_trip_rating', 'explicit_like', 'explicit_dislike')",
            name="ck_preference_events_type",
        ),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_preference_events_idempotency"),
    )
    op.create_index("ix_preference_events_profile_created", "preference_events", ["profile_id", "created_at"])
    op.create_index("ix_preference_events_trip", "preference_events", ["trip_id"])


def downgrade() -> None:
    op.drop_table("preference_events")
    op.drop_table("trip_feedback")
    op.drop_table("trips")
    op.drop_table("profile_intakes")
    op.drop_table("profiles")
    op.drop_table("sessions")
    op.drop_table("users")
