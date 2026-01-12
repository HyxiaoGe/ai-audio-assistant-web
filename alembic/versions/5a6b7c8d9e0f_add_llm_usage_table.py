"""add llm usage table

Revision ID: 5a6b7c8d9e0f
Revises: 4e5f6a7b8c9d
Create Date: 2026-01-10 13:05:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "5a6b7c8d9e0f"
down_revision = "4e5f6a7b8c9d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "llm_usages",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "task_id",
            sa.dialects.postgresql.UUID(as_uuid=False),
            sa.ForeignKey("tasks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("model_id", sa.String(length=100), nullable=True),
        sa.Column("call_type", sa.String(length=50), nullable=False),
        sa.Column("summary_type", sa.String(length=50), nullable=True),
        sa.Column("status", sa.String(length=20), server_default=sa.text("'success'")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_llm_usages_user", "llm_usages", ["user_id"])
    op.create_index("idx_llm_usages_provider", "llm_usages", ["provider"])
    op.create_index("idx_llm_usages_created_at", "llm_usages", ["created_at"])
    op.create_index("idx_llm_usages_task", "llm_usages", ["task_id"])
    op.create_index("idx_llm_usages_user_created_at", "llm_usages", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("idx_llm_usages_user_created_at", table_name="llm_usages")
    op.drop_index("idx_llm_usages_task", table_name="llm_usages")
    op.drop_index("idx_llm_usages_created_at", table_name="llm_usages")
    op.drop_index("idx_llm_usages_provider", table_name="llm_usages")
    op.drop_index("idx_llm_usages_user", table_name="llm_usages")
    op.drop_table("llm_usages")
