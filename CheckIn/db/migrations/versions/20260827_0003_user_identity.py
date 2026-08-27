"""Add username, name, and phone to users; backfill unique usernames.

Revision ID: 20260827_0003
Revises: 20260826_0002
Create Date: 2026-08-27
"""

import re

from alembic import op
import sqlalchemy as sa

revision = "20260827_0003"
down_revision = "20260826_0002"
branch_labels = None
depends_on = None


def _normalize(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9_-]+", "-", value.strip().lower().lstrip("@")).strip("-_")
    return cleaned[:30]


def upgrade() -> None:
    op.add_column("users", sa.Column("username", sa.String(40)))
    op.add_column("users", sa.Column("name", sa.String(160)))
    op.add_column("users", sa.Column("phone", sa.String(32)))

    connection = op.get_bind()
    rows = connection.execute(sa.text(
        "SELECT user_id, email FROM users WHERE username IS NULL OR username = ''"
    )).fetchall()
    taken: set[str] = set()
    for user_id, email in rows:
        base = _normalize(str(email).split("@", 1)[0]) or "traveler"
        candidate = base
        suffix = 2
        while candidate in taken:
            candidate = f"{base}-{suffix}"
            suffix += 1
        taken.add(candidate)
        connection.execute(
            sa.text("UPDATE users SET username = :username WHERE user_id = :user_id"),
            {"username": candidate, "user_id": user_id},
        )

    op.create_index(
        "uq_users_username_lower", "users", [sa.text("lower(username)")], unique=True
    )


def downgrade() -> None:
    op.drop_index("uq_users_username_lower", table_name="users")
    op.drop_column("users", "phone")
    op.drop_column("users", "name")
    op.drop_column("users", "username")
