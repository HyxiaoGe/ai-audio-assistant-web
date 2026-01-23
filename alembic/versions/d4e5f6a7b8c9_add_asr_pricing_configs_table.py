"""Add ASR pricing configs table

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-01-24 10:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create asr_pricing_configs table
    op.create_table(
        "asr_pricing_configs",
        # Primary key
        sa.Column(
            "id",
            sa.UUID(as_uuid=False),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        # Provider identification
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("variant", sa.String(length=30), nullable=False),
        # Pricing information
        sa.Column("cost_per_hour", sa.Float(), nullable=False),
        # Platform free quota
        sa.Column(
            "free_quota_seconds",
            sa.Float(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "reset_period",
            sa.String(length=20),
            server_default=sa.text("'none'"),
            nullable=False,
        ),
        # Status
        sa.Column(
            "is_enabled",
            sa.Boolean(),
            server_default=sa.text("true"),
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
        sa.UniqueConstraint("provider", "variant", name="uk_asr_pricing_configs_provider_variant"),
    )

    # Create index for quick lookups
    op.create_index(
        "idx_asr_pricing_configs_provider",
        "asr_pricing_configs",
        ["provider", "variant"],
    )
    op.create_index(
        "idx_asr_pricing_configs_enabled",
        "asr_pricing_configs",
        ["is_enabled"],
    )

    # Seed initial pricing data
    # Data source:
    # - Tencent: https://cloud.tencent.com/document/product/1093/35686
    # - Aliyun: https://help.aliyun.com/zh/isi/product-overview/billing-10
    # - Volcengine: https://www.volcengine.com/docs/6561/1359370
    op.execute(
        """
        INSERT INTO asr_pricing_configs (provider, variant, cost_per_hour, free_quota_seconds, reset_period)
        VALUES
            -- Tencent Cloud
            ('tencent', 'file', 1.25, 0, 'none'),
            ('tencent', 'file_fast', 3.10, 18000, 'monthly'),
            -- Aliyun (no free quota, pay-as-you-go)
            ('aliyun', 'file', 2.50, 0, 'none'),
            ('aliyun', 'file_fast', 3.30, 0, 'none'),
            -- Volcengine
            ('volcengine', 'file', 0.80, 72000, 'yearly'),
            ('volcengine', 'file_fast', 1.20, 0, 'none')
        """
    )


def downgrade() -> None:
    op.drop_index("idx_asr_pricing_configs_enabled", table_name="asr_pricing_configs")
    op.drop_index("idx_asr_pricing_configs_provider", table_name="asr_pricing_configs")
    op.drop_table("asr_pricing_configs")
