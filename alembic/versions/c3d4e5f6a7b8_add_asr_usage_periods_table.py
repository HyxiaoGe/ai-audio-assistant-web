"""Add ASR usage periods table and extend ASR usages

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-01-23 12:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "c3d4e5f6a7b8"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Create asr_usage_periods table
    op.create_table(
        "asr_usage_periods",
        # Primary key
        sa.Column(
            "id",
            sa.UUID(as_uuid=False),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        # Owner (NULL = global)
        sa.Column("owner_user_id", sa.UUID(as_uuid=False), nullable=True),
        # Provider info
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("variant", sa.String(length=30), nullable=False),
        # Period info
        sa.Column("period_type", sa.String(length=10), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        # Usage stats
        sa.Column(
            "used_seconds",
            sa.Float(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "free_quota_used",
            sa.Float(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "paid_seconds",
            sa.Float(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "total_cost",
            sa.Float(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        # Timestamps
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
        # Constraints
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_user_id",
            "provider",
            "variant",
            "period_type",
            "period_start",
            name="uk_asr_usage_periods_unique",
        ),
    )

    # Create indexes
    op.create_index(
        "idx_asr_usage_periods_provider",
        "asr_usage_periods",
        ["provider", "variant"],
    )
    op.create_index(
        "idx_asr_usage_periods_period",
        "asr_usage_periods",
        ["period_type", "period_start", "period_end"],
    )
    op.create_index(
        "idx_asr_usage_periods_owner",
        "asr_usage_periods",
        ["owner_user_id"],
    )

    # 2. Extend asr_usages table with free quota fields
    op.add_column(
        "asr_usages",
        sa.Column(
            "free_quota_consumed",
            sa.Float(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.add_column(
        "asr_usages",
        sa.Column(
            "paid_duration_seconds",
            sa.Float(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.add_column(
        "asr_usages",
        sa.Column(
            "actual_paid_cost",
            sa.Float(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    # Remove columns from asr_usages
    op.drop_column("asr_usages", "actual_paid_cost")
    op.drop_column("asr_usages", "paid_duration_seconds")
    op.drop_column("asr_usages", "free_quota_consumed")

    # Drop indexes
    op.drop_index("idx_asr_usage_periods_owner", table_name="asr_usage_periods")
    op.drop_index("idx_asr_usage_periods_period", table_name="asr_usage_periods")
    op.drop_index("idx_asr_usage_periods_provider", table_name="asr_usage_periods")

    # Drop table
    op.drop_table("asr_usage_periods")
