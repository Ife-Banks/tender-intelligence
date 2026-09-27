"""Add reasoning_effort fields to LLM profiles.

Revision ID: 0018_llm_reasoning_effort
Revises: 0017_llm_protocol_fields
"""
import sqlalchemy as sa
from alembic import op

revision = "0018_llm_reasoning_effort"
down_revision = "0017_llm_protocol_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("llm_profiles") as batch_op:
        batch_op.add_column(
            sa.Column(
                "supports_reasoning_effort",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.add_column(
            sa.Column("reasoning_effort", sa.String(length=32), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("llm_profiles") as batch_op:
        batch_op.drop_column("reasoning_effort")
        batch_op.drop_column("supports_reasoning_effort")
