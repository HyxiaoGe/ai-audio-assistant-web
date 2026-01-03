"""add_performance_indexes

Revision ID: 644f83942f7b
Revises: 4f6d9b8c2f1a
Create Date: 2025-12-31 11:26:27.192907

"""
from alembic import op
import sqlalchemy as sa



revision = '644f83942f7b'
down_revision = '4f6d9b8c2f1a'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Tasks table indexes for common queries
    op.create_index(
        'ix_tasks_user_status_created',
        'tasks',
        ['user_id', 'status', 'created_at'],
        unique=False
    )
    op.create_index(
        'ix_tasks_created_at',
        'tasks',
        ['created_at'],
        unique=False
    )

    # Transcripts table index for ordered retrieval
    op.create_index(
        'ix_transcripts_task_sequence',
        'transcripts',
        ['task_id', 'sequence'],
        unique=False
    )

    # Summaries table index for task lookup
    op.create_index(
        'ix_summaries_task_type',
        'summaries',
        ['task_id', 'summary_type', 'is_active'],
        unique=False
    )


def downgrade() -> None:
    op.drop_index('ix_summaries_task_type', table_name='summaries')
    op.drop_index('ix_transcripts_task_sequence', table_name='transcripts')
    op.drop_index('ix_tasks_created_at', table_name='tasks')
    op.drop_index('ix_tasks_user_status_created', table_name='tasks')
