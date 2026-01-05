"""add owner user id to service configs

Revision ID: aa2b7f8c1e23
Revises: c9f3c1d2e8ab
Create Date: 2026-01-05 21:05:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "aa2b7f8c1e23"
down_revision = "c9f3c1d2e8ab"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "service_configs",
        sa.Column("owner_user_id", sa.UUID(as_uuid=False), nullable=True),
    )
    op.create_foreign_key(
        "fk_service_configs_owner",
        "service_configs",
        "users",
        ["owner_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.drop_constraint(
        "uk_service_configs_type_provider",
        "service_configs",
        type_="unique",
    )
    op.create_unique_constraint(
        "uk_service_configs_type_provider_owner",
        "service_configs",
        ["service_type", "provider", "owner_user_id"],
    )
    op.create_index(
        "idx_service_configs_owner",
        "service_configs",
        ["owner_user_id"],
        unique=False,
    )
    op.create_index(
        "ux_service_configs_global",
        "service_configs",
        ["service_type", "provider"],
        unique=True,
        postgresql_where=sa.text("owner_user_id IS NULL"),
    )

    op.add_column(
        "service_config_history",
        sa.Column("owner_user_id", sa.UUID(as_uuid=False), nullable=True),
    )
    op.create_foreign_key(
        "fk_service_config_history_owner",
        "service_config_history",
        "users",
        ["owner_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.drop_index("idx_service_config_history_key", table_name="service_config_history")
    op.create_index(
        "idx_service_config_history_key",
        "service_config_history",
        ["service_type", "provider", "owner_user_id", "version"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_service_config_history_key", table_name="service_config_history")
    op.create_index(
        "idx_service_config_history_key",
        "service_config_history",
        ["service_type", "provider", "version"],
        unique=False,
    )
    op.drop_constraint(
        "fk_service_config_history_owner",
        "service_config_history",
        type_="foreignkey",
    )
    op.drop_column("service_config_history", "owner_user_id")

    op.drop_index("ux_service_configs_global", table_name="service_configs")
    op.drop_index("idx_service_configs_owner", table_name="service_configs")
    op.drop_constraint(
        "uk_service_configs_type_provider_owner",
        "service_configs",
        type_="unique",
    )
    op.create_unique_constraint(
        "uk_service_configs_type_provider",
        "service_configs",
        ["service_type", "provider"],
    )
    op.drop_constraint(
        "fk_service_configs_owner",
        "service_configs",
        type_="foreignkey",
    )
    op.drop_column("service_configs", "owner_user_id")
