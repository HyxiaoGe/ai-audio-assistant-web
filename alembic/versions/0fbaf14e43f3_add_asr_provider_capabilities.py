"""add_asr_provider_capabilities

Revision ID: 0fbaf14e43f3
Revises: 6bb23b8bc4d0
Create Date: 2026-01-24 11:29:16.519604

"""

from alembic import op
import sqlalchemy as sa


revision = "0fbaf14e43f3"
down_revision = "6bb23b8bc4d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. 添加新字段
    op.add_column(
        "asr_pricing_configs",
        sa.Column(
            "quality_score", sa.Float(), server_default=sa.text("0.8"), nullable=False
        ),
    )
    op.add_column(
        "asr_pricing_configs",
        sa.Column(
            "supports_diarization",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "asr_pricing_configs",
        sa.Column(
            "supports_word_level",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )

    # 2. 初始化提供商能力数据
    # Tencent: 支持说话人分离和词级时间戳，质量最高
    op.execute(
        """
        UPDATE asr_pricing_configs
        SET quality_score = 0.90,
            supports_diarization = true,
            supports_word_level = true
        WHERE provider = 'tencent'
        """
    )

    # Aliyun: 不支持特殊功能，质量中等
    op.execute(
        """
        UPDATE asr_pricing_configs
        SET quality_score = 0.85,
            supports_diarization = false,
            supports_word_level = false
        WHERE provider = 'aliyun'
        """
    )

    # Volcengine: 不支持特殊功能，质量稍低但成本最低
    op.execute(
        """
        UPDATE asr_pricing_configs
        SET quality_score = 0.80,
            supports_diarization = false,
            supports_word_level = false
        WHERE provider = 'volcengine'
        """
    )


def downgrade() -> None:
    op.drop_column("asr_pricing_configs", "supports_word_level")
    op.drop_column("asr_pricing_configs", "supports_diarization")
    op.drop_column("asr_pricing_configs", "quality_score")
