"""Persist Stage A triage decisions.

Revision ID: 0011_triage_results
Revises: 0010_deadline_resolution
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011_triage_results"
down_revision = "0010_deadline_resolution"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "triage_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "tender_id",
            sa.Integer(),
            sa.ForeignKey("tenders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("run_history.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("correlation_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("reasons", sa.JSON(), nullable=True),
        sa.Column(
            "llm_profile_id",
            sa.Integer(),
            sa.ForeignKey("llm_profiles.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("model", sa.String(length=255), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_triage_results_tender_id", "triage_results", ["tender_id"])
    op.create_index("ix_triage_results_correlation_id", "triage_results", ["correlation_id"])
    op.create_index("ix_triage_results_run_id", "triage_results", ["run_id"])
    op.create_index("ix_triage_results_status", "triage_results", ["status"])


def downgrade() -> None:
    op.drop_index("ix_triage_results_status", table_name="triage_results")
    op.drop_index("ix_triage_results_correlation_id", table_name="triage_results")
    op.drop_index("ix_triage_results_run_id", table_name="triage_results")
    op.drop_index("ix_triage_results_tender_id", table_name="triage_results")
    op.drop_table("triage_results")
