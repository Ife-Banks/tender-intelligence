"""Deadline resolution provenance — separate table (Prompt 12.1).

A new ``tender_deadline_resolution`` table holds the resolution outcome for each tender.
One-to-one with ``tenders`` via ``tender_id`` (unique FK).  Separated from ``tenders``
so that the ``Tender`` ORM model and all existing tests at earlier migration checkpoints
are completely unaffected — deadline resolution state is additive infrastructure.

Columns
-------
tender_id            FK → tenders.id (unique, cascade delete)
deadline_resolved    UTC datetime — best deadline from the full source hierarchy
                     (listing → detail → document).  NULL when UNRESOLVED/CONFLICTING.
deadline_source      One of: listing_page / detail_page / document / conflicting / unresolved
deadline_evidence    Bounded JSON provenance blob (≤200-char excerpt; no secrets)
deadline_resolved_at UTC timestamp when the resolution stage last ran (Timeline trigger)
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010_deadline_resolution"
down_revision = "0009_provider_usage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tender_deadline_resolution",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "tender_id",
            sa.Integer(),
            sa.ForeignKey("tenders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("deadline_resolved", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deadline_source", sa.String(length=32), nullable=True),
        sa.Column("deadline_evidence", sa.JSON(), nullable=True),
        sa.Column("deadline_resolved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "uq_tender_deadline_resolution_tender",
        "tender_deadline_resolution",
        ["tender_id"],
        unique=True,
    )
    op.create_index(
        "ix_tender_deadline_resolution_source",
        "tender_deadline_resolution",
        ["deadline_source"],
    )
    op.create_index(
        "ix_tender_deadline_resolution_resolved_at",
        "tender_deadline_resolution",
        ["deadline_resolved_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_tender_deadline_resolution_resolved_at",
                  table_name="tender_deadline_resolution")
    op.drop_index("ix_tender_deadline_resolution_source",
                  table_name="tender_deadline_resolution")
    op.drop_index("uq_tender_deadline_resolution_tender",
                  table_name="tender_deadline_resolution")
    op.drop_table("tender_deadline_resolution")
