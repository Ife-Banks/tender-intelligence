"""mail circuit breaker: persist the consecutive-failure counter

The breaker's ``breaker_state``/``breaker_until`` columns were never enough: the
consecutive-failure count is the input to the OPEN transition, and rebuilding the breaker
from a fresh count every message meant a provider that fails once per message (exactly what a
downtime looks like) could never trip a threshold of 3. Persisting the count makes the
CLOSED → OPEN transition restart-safe across messages. Non-destructive: additive column with
a server default, so existing provider rows remain valid (PROJECT_RULES #14).

Revision ID: 0006_mail_breaker_count
Revises: 0005_notif_dedupe_trace
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_mail_breaker_count"
down_revision = "0005_notif_dedupe_trace"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "mail_providers",
        sa.Column(
            "breaker_failures", sa.Integer(), nullable=False, server_default="0"
        ),
    )


def downgrade() -> None:
    with op.batch_alter_table("mail_providers") as batch_op:
        batch_op.drop_column("breaker_failures")
