"""Add protocol and provider_name fields to LLM profiles.

Revision ID: 0017_llm_protocol_fields
Revises: 0016_llm_capability_fields
"""
import sqlalchemy as sa
from alembic import op

revision = "0017_llm_protocol_fields"
down_revision = "0016_llm_capability_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("llm_profiles") as batch_op:
        batch_op.add_column(
            sa.Column("provider_name", sa.String(length=255), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "protocol",
                sa.String(length=32),
                nullable=False,
                server_default="openai_compatible",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("llm_profiles") as batch_op:
        batch_op.drop_column("protocol")
        batch_op.drop_column("provider_name")
