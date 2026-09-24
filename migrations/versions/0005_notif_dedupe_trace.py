"""notification delivery: correlation traceability + the unique dedupe index

Prompt 11 §8 (attempt recording with correlation context) and docs/03's traceability
principle need a ``correlation_id`` on both notification records so a failed email can be
followed back to the tender's timeline alongside every other stage. Non-destructive:
both columns are nullable, so existing rows stay valid (PROJECT_RULES #14).

The second change fixes a real gap: the ``NotificationLog`` model declares a **unique**
index on ``dedupe_key`` (docs/03 §3.2, docs/08 §8.12), but migration 0001 never created it,
so nothing at the database level prevented duplicate notification rows. Prompt 11 §12's
concurrency-safe idempotency relies on the constraint, so it is added here for the exact
name the model declares. Before creating it, legacy duplicate rows are preserved and given
archival keys; no historical notification or attempt is deleted. On a duplicate-free
baseline the reconciliation is a no-op.

Revision ID: 0005_notif_dedupe_trace
Revises: 0004_run_history_stage_state
"""

from __future__ import annotations

import hashlib

import sqlalchemy as sa
from alembic import op

revision = "0005_notif_dedupe_trace"
down_revision = "0004_run_history_stage_state"
branch_labels = None
depends_on = None


def _archive_legacy_duplicate_keys() -> None:
    """Make pre-Prompt-11 duplicate rows installable without deleting audit history.

    ``0001`` did not create the unique index, so an older installation may contain more than
    one row for a dedupe key.  Keep the earliest row under the original key and give every
    later historical row a deterministic archival key.  No notification or attempt row is
    deleted; the old key remains the lookup winner and all history stays auditable.
    """

    bind = op.get_bind()
    duplicate_keys = bind.execute(
        sa.text(
            """
            SELECT dedupe_key
            FROM notification_logs
            WHERE dedupe_key IS NOT NULL
            GROUP BY dedupe_key
            HAVING COUNT(*) > 1
            """
        )
    ).scalars().all()
    note = "legacy duplicate dedupe key archived during migration 0005"
    for key in duplicate_keys:
        rows = bind.execute(
            sa.text(
                "SELECT id FROM notification_logs "
                "WHERE dedupe_key = :key ORDER BY id"
            ),
            {"key": key},
        ).scalars().all()
        for row_id in rows[1:]:
            suffix = f":legacy-duplicate:{row_id}"
            candidate = f"{str(key)[: max(1, 255 - len(suffix))]}{suffix}"
            collision = bind.execute(
                sa.text("SELECT id FROM notification_logs WHERE dedupe_key = :candidate"),
                {"candidate": candidate},
            ).first()
            if collision is not None:
                digest = hashlib.sha256(f"{key}:{row_id}".encode()).hexdigest()
                candidate = f"legacy-duplicate:{digest}:{row_id}"
            bind.execute(
                sa.text(
                    "UPDATE notification_logs "
                    "SET dedupe_key = :candidate, "
                    "error = CASE WHEN error IS NULL THEN :note "
                    "ELSE error || ' | ' || :note END "
                    "WHERE id = :row_id"
                ),
                {"candidate": candidate, "note": note, "row_id": row_id},
            )


def upgrade() -> None:
    op.add_column(
        "notification_logs", sa.Column("correlation_id", sa.String(length=36), nullable=True)
    )
    op.add_column(
        "notification_attempts",
        sa.Column("correlation_id", sa.String(length=36), nullable=True),
    )
    _archive_legacy_duplicate_keys()
    op.create_index(
        "ix_notifications_dedupe_key", "notification_logs", ["dedupe_key"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_notifications_dedupe_key", table_name="notification_logs")
    with op.batch_alter_table("notification_attempts") as batch_op:
        batch_op.drop_column("correlation_id")
    with op.batch_alter_table("notification_logs") as batch_op:
        batch_op.drop_column("correlation_id")
