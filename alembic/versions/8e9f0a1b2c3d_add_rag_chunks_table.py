"""add rag chunks table

Revision ID: 8e9f0a1b2c3d
Revises: 5a6b7c8d9e0f
Create Date: 2026-01-13 19:10:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "8e9f0a1b2c3d"
down_revision = "5a6b7c8d9e0f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rag_chunks",
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
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "transcript_id",
            sa.dialects.postgresql.UUID(as_uuid=False),
            sa.ForeignKey("transcripts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("start_time", sa.DECIMAL(10, 3), nullable=True),
        sa.Column("end_time", sa.DECIMAL(10, 3), nullable=True),
        sa.Column("speaker_id", sa.String(length=50), nullable=True),
        sa.Column("embedding", sa.ARRAY(sa.Float()), nullable=True),
        sa.Column("embedding_model", sa.String(length=100), nullable=True),
        sa.Column("embedding_dim", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "chunk_index", name="uk_rag_chunks_task"),
    )
    op.create_index("idx_rag_chunks_user", "rag_chunks", ["user_id"])
    op.create_index("idx_rag_chunks_task", "rag_chunks", ["task_id"])
    op.create_index("idx_rag_chunks_transcript", "rag_chunks", ["transcript_id"])


def downgrade() -> None:
    op.drop_index("idx_rag_chunks_transcript", table_name="rag_chunks")
    op.drop_index("idx_rag_chunks_task", table_name="rag_chunks")
    op.drop_index("idx_rag_chunks_user", table_name="rag_chunks")
    op.drop_table("rag_chunks")
