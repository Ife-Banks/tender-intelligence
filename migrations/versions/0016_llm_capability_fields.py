"""Add capability fields for provider-agnostic structured output and reasoning.

Revision ID: 0016_llm_capability_fields
Revises: 0015_run_trigger
"""
import sqlalchemy as sa
from alembic import op

revision = "0016_llm_capability_fields"
down_revision = "0015_run_trigger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("llm_profiles") as batch_op:
        batch_op.add_column(
            sa.Column("supports_response_format", sa.Boolean(), nullable=False, server_default=sa.true())
        )
        batch_op.add_column(
            sa.Column("supports_include_reasoning", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.add_column(
            sa.Column("supports_chat_template_kwargs", sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    with op.batch_alter_table("llm_profiles") as batch_op:
        batch_op.drop_column("supports_chat_template_kwargs")
        batch_op.drop_column("supports_include_reasoning")
        batch_op.drop_column("supports_response_format")
