"""add asr quotas table

Revision ID: d3b2a1c9f2e7
Revises: 7b6a0f1d4c8f
Create Date: 2026-01-06 16:05:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "d3b2a1c9f2e7"
down_revision = "7b6a0f1d4c8f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "asr_quotas",
        sa.Column(
            "id",
            sa.UUID(as_uuid=False),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("window_type", sa.String(length=10), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("quota_seconds", sa.Integer(), nullable=False),
        sa.Column("used_seconds", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider",
            "window_type",
            "window_start",
            name="uk_asr_quotas_provider_window",
        ),
    )


def downgrade() -> None:
    op.drop_table("asr_quotas")
