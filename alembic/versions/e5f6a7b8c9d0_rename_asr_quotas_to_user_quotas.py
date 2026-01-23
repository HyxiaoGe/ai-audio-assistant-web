"""Rename asr_quotas to asr_user_quotas

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-01-24 10:01:00.000000

"""

from alembic import op


revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Rename table
    op.rename_table("asr_quotas", "asr_user_quotas")

    # Rename unique constraint
    op.execute(
        """
        ALTER TABLE asr_user_quotas
        RENAME CONSTRAINT uk_asr_quotas_provider_window TO uk_asr_user_quotas_provider_window
        """
    )


def downgrade() -> None:
    # Rename constraint back
    op.execute(
        """
        ALTER TABLE asr_user_quotas
        RENAME CONSTRAINT uk_asr_user_quotas_provider_window TO uk_asr_quotas_provider_window
        """
    )

    # Rename table back
    op.rename_table("asr_user_quotas", "asr_quotas")
