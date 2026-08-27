"""Add the taste-aware LLM research cache table.

Revision ID: 20260826_0002
Revises: 20260731_0001
Create Date: 2026-08-26
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260826_0002"
down_revision = "20260731_0001"
branch_labels = None
depends_on = None


def _types():
    if op.get_bind().dialect.name == "postgresql":
        return postgresql.UUID(as_uuid=False), postgresql.JSONB()
    return sa.String(36), sa.JSON()


def upgrade() -> None:
    uuid_type, json_type = _types()
    op.create_table(
        "llm_research_cache",
        sa.Column("cache_id", uuid_type, primary_key=True),
        sa.Column("exact_key", sa.String(64), nullable=False),
        sa.Column("taste_vector", json_type, nullable=False),
        sa.Column("request_facts", json_type, nullable=False),
        sa.Column("payload", json_type, nullable=False),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index(
        "ix_llm_research_cache_key_created",
        "llm_research_cache",
        ["exact_key", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_llm_research_cache_key_created", table_name="llm_research_cache")
    op.drop_table("llm_research_cache")
