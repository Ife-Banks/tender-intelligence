"""Per-profile sampling/reasoning controls and request provenance.

Revision ID: 0014_llm_sampling_provenance
Revises: 0013_kb_retrieval_provenance
"""
import sqlalchemy as sa
from alembic import op

revision = "0014_llm_sampling_provenance"
down_revision = "0013_kb_retrieval_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("llm_profiles", sa.Column("top_p", sa.Float(), nullable=True))
    op.add_column("llm_profiles", sa.Column("reasoning_budget", sa.Integer(), nullable=True))
    op.add_column(
        "llm_profiles",
        sa.Column("enable_thinking", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("llm_calls", sa.Column("request_config", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("llm_calls", "request_config")
    op.drop_column("llm_profiles", "enable_thinking")
    op.drop_column("llm_profiles", "reasoning_budget")
    op.drop_column("llm_profiles", "top_p")
