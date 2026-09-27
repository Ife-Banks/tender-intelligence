"""Record whether a run was scheduled or operator initiated.

Revision ID: 0015_run_trigger
Revises: 0014_llm_sampling_provenance
"""
import sqlalchemy as sa
from alembic import op

revision = "0015_run_trigger"
down_revision = "0014_llm_sampling_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("run_history", sa.Column("trigger", sa.String(length=32), nullable=True))
    with op.batch_alter_table("run_history") as batch_op:
        batch_op.add_column(sa.Column("tender_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_run_history_tender_id_tenders", "tenders", ["tender_id"], ["id"], ondelete="SET NULL"
        )
    op.create_index("ix_run_history_tender_id", "run_history", ["tender_id"])


def downgrade() -> None:
    op.drop_index("ix_run_history_tender_id", table_name="run_history")
    with op.batch_alter_table("run_history") as batch_op:
        batch_op.drop_constraint("fk_run_history_tender_id_tenders", type_="foreignkey")
        batch_op.drop_column("tender_id")
    op.drop_column("run_history", "trigger")
