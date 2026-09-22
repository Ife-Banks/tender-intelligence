"""Relax documents.checksum to nullable (docs/03 §3.2, prompt 06 §7).

Revision ID: 0003_documents_checksum_nullable
Revises: 0002_run_history_counts
Create Date: 2026-09-22

Document rows must be created in the initial ``pending`` state before the source file
has been downloaded (prompt 08 owns downloads/checksums). The column was NOT NULL in
0001, but the model annotation (``Mapped[str | None]``) already anticipated nullability.
This is a purely additive relaxation: NULLs stay distinct under ``uq_documents_tender_checksum``
on both SQLite and PostgreSQL, so a tender may have multiple pending documents.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_documents_checksum_nullable"
down_revision = "0002_run_history_counts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("documents") as batch_op:
        batch_op.alter_column("checksum", existing_type=sa.String(length=64), nullable=True)


def downgrade() -> None:
    with op.batch_alter_table("documents") as batch_op:
        batch_op.alter_column("checksum", existing_type=sa.String(length=64), nullable=False)
