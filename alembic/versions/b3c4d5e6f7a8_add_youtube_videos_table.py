"""add youtube_videos table and extend subscriptions

Revision ID: b3c4d5e6f7a8
Revises: a2b3c4d5e6f7
Create Date: 2026-01-25 12:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "b3c4d5e6f7a8"
down_revision = "a2b3c4d5e6f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add new columns to youtube_subscriptions
    op.add_column(
        "youtube_subscriptions",
        sa.Column("uploads_playlist_id", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "youtube_subscriptions",
        sa.Column("videos_synced_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Create youtube_videos table
    op.create_table(
        "youtube_videos",
        sa.Column(
            "id",
            sa.UUID(as_uuid=False),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "subscription_id",
            sa.UUID(as_uuid=False),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.UUID(as_uuid=False),
            nullable=False,
        ),
        sa.Column("video_id", sa.String(length=20), nullable=False),
        sa.Column("channel_id", sa.String(length=100), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("thumbnail_url", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("view_count", sa.BigInteger(), nullable=True),
        sa.Column("like_count", sa.BigInteger(), nullable=True),
        sa.Column("comment_count", sa.BigInteger(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["subscription_id"],
            ["youtube_subscriptions.id"],
            name="fk_youtube_videos_subscription",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_youtube_videos_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "video_id",
            name="uk_youtube_videos_user_video",
        ),
    )

    # Create indexes
    op.create_index(
        "idx_youtube_videos_channel_published",
        "youtube_videos",
        ["user_id", "channel_id", "published_at"],
        unique=False,
    )
    op.create_index(
        "idx_youtube_videos_user_published",
        "youtube_videos",
        ["user_id", "published_at"],
        unique=False,
    )
    op.create_index(
        "idx_youtube_videos_subscription",
        "youtube_videos",
        ["subscription_id"],
        unique=False,
    )


def downgrade() -> None:
    # Drop youtube_videos table and indexes
    op.drop_index("idx_youtube_videos_subscription", table_name="youtube_videos")
    op.drop_index("idx_youtube_videos_user_published", table_name="youtube_videos")
    op.drop_index("idx_youtube_videos_channel_published", table_name="youtube_videos")
    op.drop_table("youtube_videos")

    # Remove columns from youtube_subscriptions
    op.drop_column("youtube_subscriptions", "videos_synced_at")
    op.drop_column("youtube_subscriptions", "uploads_playlist_id")
