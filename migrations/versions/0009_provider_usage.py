"""durable provider rate-limit accounting

Provider daily and per-minute limits must survive worker restarts and be shared by workers.
The usage row is an additive implementation table keyed one-to-one by provider; it contains
only counters and timestamps, never credentials or message content.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009_provider_usage"
down_revision = "0008_notif_attempt_provider"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mail_provider_usage",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider_id", sa.Integer(), nullable=False),
        sa.Column("minute_started", sa.DateTime(timezone=True), nullable=True),
        sa.Column("minute_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("day_started", sa.DateTime(timezone=True), nullable=True),
        sa.Column("day_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["provider_id"], ["mail_providers.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_mail_provider_usage_provider_id", "mail_provider_usage", ["provider_id"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_mail_provider_usage_provider_id", table_name="mail_provider_usage")
    op.drop_table("mail_provider_usage")
