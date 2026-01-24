"""add youtube_subscriptions table

Revision ID: a2b3c4d5e6f7
Revises: 0fbaf14e43f3
Create Date: 2026-01-24 22:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "a2b3c4d5e6f7"
down_revision = "0fbaf14e43f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "youtube_subscriptions",
        sa.Column(
            "id",
            sa.UUID(as_uuid=False),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.UUID(as_uuid=False),
            nullable=False,
        ),
        sa.Column("channel_id", sa.String(length=100), nullable=False),
        sa.Column("channel_title", sa.String(length=255), nullable=False),
        sa.Column("channel_thumbnail", sa.Text(), nullable=True),
        sa.Column("channel_description", sa.Text(), nullable=True),
        sa.Column("subscribed_at", sa.DateTime(timezone=True), nullable=True),
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
            ["user_id"],
            ["users.id"],
            name="fk_youtube_subscriptions_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "channel_id",
            name="uk_youtube_subscriptions_user_channel",
        ),
    )
    op.create_index(
        "idx_youtube_subscriptions_user_id",
        "youtube_subscriptions",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_youtube_subscriptions_user_id", table_name="youtube_subscriptions")
    op.drop_table("youtube_subscriptions")
