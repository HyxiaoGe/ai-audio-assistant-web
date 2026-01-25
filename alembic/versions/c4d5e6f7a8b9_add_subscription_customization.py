"""add subscription customization fields

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
Create Date: 2026-01-25 16:00:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "c4d5e6f7a8b9"
down_revision = "b3c4d5e6f7a8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add customization columns to youtube_subscriptions
    op.add_column(
        "youtube_subscriptions",
        sa.Column("is_hidden", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column(
        "youtube_subscriptions",
        sa.Column("sync_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    )
    op.add_column(
        "youtube_subscriptions",
        sa.Column("is_starred", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column(
        "youtube_subscriptions",
        sa.Column("auto_transcribe", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column(
        "youtube_subscriptions",
        sa.Column("auto_transcribe_max_duration", sa.Integer(), nullable=True),
    )
    op.add_column(
        "youtube_subscriptions",
        sa.Column("auto_transcribe_language", sa.String(10), nullable=True),
    )
    op.add_column(
        "youtube_subscriptions",
        sa.Column("avg_publish_interval_hours", sa.Float(), nullable=True),
    )
    op.add_column(
        "youtube_subscriptions",
        sa.Column("last_publish_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "youtube_subscriptions",
        sa.Column("next_sync_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Create partial index for starred channels
    op.create_index(
        "idx_youtube_subscriptions_starred",
        "youtube_subscriptions",
        ["user_id", "is_starred"],
        postgresql_where=sa.text("is_starred = true"),
    )

    # Create partial index for next sync scheduling
    op.create_index(
        "idx_youtube_subscriptions_next_sync",
        "youtube_subscriptions",
        ["next_sync_at"],
        postgresql_where=sa.text("sync_enabled = true"),
    )

    # Create youtube_auto_transcribe_logs table
    op.create_table(
        "youtube_auto_transcribe_logs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=False),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("video_id", sa.String(20), nullable=False),
        sa.Column("subscription_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("skip_reason", sa.String(100), nullable=True),
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
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["subscription_id"],
            ["youtube_subscriptions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["tasks.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "video_id", name="uk_youtube_auto_transcribe_user_video"),
    )
    op.create_index(
        "idx_youtube_auto_transcribe_user_status",
        "youtube_auto_transcribe_logs",
        ["user_id", "status"],
    )


def downgrade() -> None:
    # Drop youtube_auto_transcribe_logs table
    op.drop_index("idx_youtube_auto_transcribe_user_status", table_name="youtube_auto_transcribe_logs")
    op.drop_table("youtube_auto_transcribe_logs")

    # Drop indexes from youtube_subscriptions
    op.drop_index("idx_youtube_subscriptions_next_sync", table_name="youtube_subscriptions")
    op.drop_index("idx_youtube_subscriptions_starred", table_name="youtube_subscriptions")

    # Drop columns from youtube_subscriptions
    op.drop_column("youtube_subscriptions", "next_sync_at")
    op.drop_column("youtube_subscriptions", "last_publish_at")
    op.drop_column("youtube_subscriptions", "avg_publish_interval_hours")
    op.drop_column("youtube_subscriptions", "auto_transcribe_language")
    op.drop_column("youtube_subscriptions", "auto_transcribe_max_duration")
    op.drop_column("youtube_subscriptions", "auto_transcribe")
    op.drop_column("youtube_subscriptions", "is_starred")
    op.drop_column("youtube_subscriptions", "sync_enabled")
    op.drop_column("youtube_subscriptions", "is_hidden")
