"""notification outbox lifecycle and attempt metadata

Prompt 11 requires a notification reservation to be durable before a network request, and
requires every provider attempt to be reconstructable.  Migration 0001 created the core rows,
but the first implementation had no ``sending``/claim state and no attempt number.  These
columns are additive and nullable/defaulted so existing notification history remains valid.

The stored ``message_payload`` contains only the prepared, provider-neutral message metadata
(recipient snapshot, attachment/link plan and rendered body); credentials and raw storage
paths are never placed in it.  It lets a restarted worker retry the same message without
reinterpreting a later verdict.

Revision ID: 0007_notif_outbox_lifecycle
Revises: 0006_mail_breaker_count
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_notif_outbox_lifecycle"
down_revision = "0006_mail_breaker_count"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "notification_logs",
        sa.Column("notification_kind", sa.String(length=16), nullable=False, server_default="new"),
    )
    op.add_column(
        "notification_logs",
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
    )
    op.add_column("notification_logs", sa.Column("message_payload", sa.JSON(), nullable=True))
    op.add_column(
        "notification_logs", sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "notification_logs", sa.Column("claim_token", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "notification_logs", sa.Column("claim_until", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "notification_logs",
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "notification_logs", sa.Column("last_error_code", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "notification_attempts",
        sa.Column("attempt_number", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "notification_attempts",
        sa.Column("possible_duplicate", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "notification_attempts",
        sa.Column("provider_message_id", sa.String(length=255), nullable=True),
    )
    op.create_index("ix_notification_logs_next_retry_at", "notification_logs", ["next_retry_at"])
    op.create_index("ix_notification_logs_claim_until", "notification_logs", ["claim_until"])


def downgrade() -> None:
    op.drop_index("ix_notification_logs_claim_until", table_name="notification_logs")
    op.drop_index("ix_notification_logs_next_retry_at", table_name="notification_logs")
    with op.batch_alter_table("notification_attempts") as batch_op:
        batch_op.drop_column("provider_message_id")
        batch_op.drop_column("possible_duplicate")
        batch_op.drop_column("attempt_number")
    with op.batch_alter_table("notification_logs") as batch_op:
        batch_op.drop_column("last_error_code")
        batch_op.drop_column("attempt_count")
        batch_op.drop_column("claim_until")
        batch_op.drop_column("claim_token")
        batch_op.drop_column("next_retry_at")
        batch_op.drop_column("message_payload")
        batch_op.drop_column("priority")
        batch_op.drop_column("notification_kind")
