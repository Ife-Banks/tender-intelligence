"""Extend run_history with prompt-05 counts and run correlation ID (docs/03 §3.2).

Revision ID: 0002_run_history_counts
Revises: 0001_initial
Create Date: 2026-09-22

Prompt 05 (§7, docs/04 §4.8) requires RunHistory to track update/unchanged counts and stamp
the run correlation ID. These three columns are additive; the lifecycle itself (RUNNING →
COMPLETED / ERRORED) is derived from ``ended_at`` + ``error_count``, not stored.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_run_history_counts"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "run_history", sa.Column("update_count", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column(
        "run_history",
        sa.Column("unchanged_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "run_history", sa.Column("correlation_id", sa.String(length=36), nullable=True)
    )
    op.create_index("ix_run_history_correlation_id", "run_history", ["correlation_id"])


def downgrade() -> None:
    op.drop_index("ix_run_history_correlation_id", table_name="run_history")
    op.drop_column("run_history", "correlation_id")
    op.drop_column("run_history", "unchanged_count")
    op.drop_column("run_history", "update_count")
