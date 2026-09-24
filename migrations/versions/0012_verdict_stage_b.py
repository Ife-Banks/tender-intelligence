"""Stage B provenance and deadline source timezone.

Revision ID: 0012_verdict_stage_b
Revises: 0011_triage_results
"""

import sqlalchemy as sa
from alembic import op

revision = "0012_verdict_stage_b"
down_revision = "0011_triage_results"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "verdicts",
        sa.Column("schema_version", sa.String(64), nullable=False, server_default="verdict.v1"),
    )
    op.add_column("verdicts", sa.Column("provider", sa.String(255), nullable=True))
    op.add_column(
        "verdicts",
        sa.Column("map_reduce_used", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("verdicts", sa.Column("run_id", sa.Integer(), nullable=True))
    op.add_column("verdicts", sa.Column("deadline_status", sa.String(16), nullable=True))
    op.add_column("verdicts", sa.Column("deadline_utc", sa.String(64), nullable=True))
    op.add_column("verdicts", sa.Column("deadline_date", sa.String(16), nullable=True))
    op.add_column("verdicts", sa.Column("deadline_time", sa.String(16), nullable=True))
    op.add_column("verdicts", sa.Column("deadline_timezone", sa.String(64), nullable=True))
    op.add_column("verdicts", sa.Column("source_timezone", sa.String(64), nullable=True))
    op.add_column("llm_calls", sa.Column("tender_id", sa.Integer(), nullable=True))
    op.add_column("llm_calls", sa.Column("run_id", sa.Integer(), nullable=True))
    op.add_column("llm_calls", sa.Column("provider", sa.String(255), nullable=True))
    op.add_column("llm_calls", sa.Column("model", sa.String(255), nullable=True))
    op.add_column(
        "tender_deadline_resolution", sa.Column("deadline_timezone", sa.String(64), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("tender_deadline_resolution", "deadline_timezone")
    for column in ("model", "provider", "run_id", "tender_id"):
        op.drop_column("llm_calls", column)
    for column in (
        "source_timezone",
        "deadline_timezone",
        "deadline_time",
        "deadline_date",
        "deadline_utc",
        "deadline_status",
        "run_id",
        "map_reduce_used",
        "provider",
        "schema_version",
    ):
        op.drop_column("verdicts", column)
