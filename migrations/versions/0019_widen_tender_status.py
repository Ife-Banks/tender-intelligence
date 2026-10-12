"""Widen ``tenders.status`` so every status value fits (C2).

``TENDER_STATUSES`` includes ``awaiting_approved_provider`` (26 characters) but the
``tenders.status`` column was created as ``String(24)`` (migration 0001).  On PostgreSQL
persisting that status raises ``value too long for type character varying(24)``; SQLite
does not enforce VARCHAR length, so tests stayed green while the production target
(Neon/PostgreSQL) crashed.  This widens the column to ``String(32)`` — enough for the
longest status (26 chars) with headroom for future states.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0019_widen_tender_status"
down_revision = "0018_llm_reasoning_effort"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tenders") as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=sa.String(length=24),
            type_=sa.String(length=32),
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("tenders") as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=sa.String(length=32),
            type_=sa.String(length=24),
            existing_nullable=False,
        )