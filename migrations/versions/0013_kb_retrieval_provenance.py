"""KB metadata and selected evidence provenance.

Revision ID: 0013_kb_retrieval_provenance
Revises: 0012_verdict_stage_b
"""
import sqlalchemy as sa
from alembic import op

revision = "0013_kb_retrieval_provenance"
down_revision = "0012_verdict_stage_b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("knowledge_base_versions", sa.Column("metadata_json", sa.JSON(), nullable=True))
    op.add_column("verdicts", sa.Column("knowledge_base_evidence", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("verdicts", "knowledge_base_evidence")
    op.drop_column("knowledge_base_versions", "metadata_json")
