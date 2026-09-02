"""Add trips.idempotency_key so trip-creation retries cannot duplicate a trip.

Revision ID: 20260901_0004
Revises: 20260827_0003
Create Date: 2026-09-01
"""

from alembic import op
import sqlalchemy as sa

revision = "20260901_0004"
down_revision = "20260827_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("trips", sa.Column("idempotency_key", sa.String(160)))
    op.create_index(
        "uq_trips_user_idempotency", "trips", ["user_id", "idempotency_key"], unique=True
    )


def downgrade() -> None:
    op.drop_index("uq_trips_user_idempotency", table_name="trips")
    op.drop_column("trips", "idempotency_key")
