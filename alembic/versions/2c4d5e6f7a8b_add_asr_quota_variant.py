"""add asr quota variant

Revision ID: 2c4d5e6f7a8b
Revises: 9d0e1f2a3b4c
Create Date: 2026-01-08 18:10:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "2c4d5e6f7a8b"
down_revision = "9d0e1f2a3b4c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "asr_quotas",
        sa.Column("variant", sa.String(length=30), nullable=False, server_default="file"),
    )
    op.execute("UPDATE asr_quotas SET variant = 'file' WHERE variant IS NULL;")

    op.drop_index("uk_asr_quotas_global_window", table_name="asr_quotas")
    op.drop_constraint("uk_asr_quotas_provider_window", "asr_quotas", type_="unique")
    op.create_unique_constraint(
        "uk_asr_quotas_provider_window",
        "asr_quotas",
        ["provider", "variant", "window_type", "window_start", "owner_user_id"],
    )
    op.create_index(
        "uk_asr_quotas_global_window",
        "asr_quotas",
        ["provider", "variant", "window_type", "window_start"],
        unique=True,
        postgresql_where=sa.text("owner_user_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uk_asr_quotas_global_window", table_name="asr_quotas")
    op.drop_constraint("uk_asr_quotas_provider_window", "asr_quotas", type_="unique")
    op.create_unique_constraint(
        "uk_asr_quotas_provider_window",
        "asr_quotas",
        ["provider", "window_type", "window_start", "owner_user_id"],
    )
    op.create_index(
        "uk_asr_quotas_global_window",
        "asr_quotas",
        ["provider", "window_type", "window_start"],
        unique=True,
        postgresql_where=sa.text("owner_user_id IS NULL"),
    )
    op.drop_column("asr_quotas", "variant")
