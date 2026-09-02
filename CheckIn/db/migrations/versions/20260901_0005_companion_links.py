"""Add companion_links: invitation records that gate linked-profile sharing.

Revision ID: 20260901_0005
Revises: 20260901_0004
Create Date: 2026-09-01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260901_0005"
down_revision = "20260901_0004"
branch_labels = None
depends_on = None


def _uuid_type():
    if op.get_bind().dialect.name == "postgresql":
        return postgresql.UUID(as_uuid=False)
    return sa.String(36)


def upgrade() -> None:
    uuid_type = _uuid_type()
    timestamp = sa.DateTime(timezone=True)

    op.create_table(
        "companion_links",
        sa.Column("link_id", uuid_type, primary_key=True),
        sa.Column("inviter_user_id", uuid_type, sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
        sa.Column("invitee_user_id", uuid_type, sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("created_at", timestamp, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("responded_at", timestamp),
        sa.UniqueConstraint("inviter_user_id", "invitee_user_id", name="uq_companion_links_pair"),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'declined', 'revoked')",
            name="ck_companion_links_status",
        ),
        sa.CheckConstraint("inviter_user_id <> invitee_user_id", name="ck_companion_links_distinct"),
    )
    op.create_index(
        "ix_companion_links_invitee_status", "companion_links", ["invitee_user_id", "status"]
    )


def downgrade() -> None:
    op.drop_index("ix_companion_links_invitee_status", table_name="companion_links")
    op.drop_table("companion_links")
