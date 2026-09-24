"""preserve provider identity on notification attempts

A provider row may later be removed while its operational history must remain readable.  The
attempt already has a foreign key; this additive nullable snapshot keeps the safe provider
name available even when the foreign key is nulled.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_notif_attempt_provider"
down_revision = "0007_notif_outbox_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "notification_attempts", sa.Column("provider_name", sa.String(length=255), nullable=True)
    )


def downgrade() -> None:
    with op.batch_alter_table("notification_attempts") as batch_op:
        batch_op.drop_column("provider_name")
