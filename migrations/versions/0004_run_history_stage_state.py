"""run_history: record the per-stage outcome of an orchestrated run

Prompt 10 §5 requires the run row created at run start to be finalised at run end with the
failed stage and its error code; §8 requires a completed *or* failed run to be
reconstructable from the row alone. Neither is expressible with the existing columns, so
four nullable columns are added:

* ``failed_stage``    — the stage that failed, NULL when the run completed
* ``error_code``      — that stage's machine-readable code from ``core.errors``
* ``stages``          — JSON map of stage name -> stage status (prompt 10 §8's six values)
* ``config_version``  — the ``Setting.version`` the run executed under (prompt 10 §3)

All four are nullable, so existing rows stay valid and the migration is non-destructive
(PROJECT_RULES #14). Run status itself remains **derived, never stored** — ``ended_at IS
NULL`` is RUNNING, ``error_count > 0`` is ERRORED, otherwise COMPLETED (docs/03 §3.2,
docs/04 §4.8) — so no status column is introduced here.

Revision ID: 0004_run_history_stage_state
Revises: 0003_documents_checksum_nullable
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_run_history_stage_state"
down_revision = "0003_documents_checksum_nullable"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("run_history", sa.Column("failed_stage", sa.String(length=64), nullable=True))
    op.add_column("run_history", sa.Column("error_code", sa.String(length=64), nullable=True))
    op.add_column("run_history", sa.Column("stages", sa.JSON(), nullable=True))
    op.add_column("run_history", sa.Column("config_version", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("run_history") as batch_op:
        batch_op.drop_column("config_version")
        batch_op.drop_column("stages")
        batch_op.drop_column("error_code")
        batch_op.drop_column("failed_stage")
