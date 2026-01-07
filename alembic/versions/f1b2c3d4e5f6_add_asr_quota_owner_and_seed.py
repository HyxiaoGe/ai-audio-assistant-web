"""add asr quota owner and seed defaults

Revision ID: f1b2c3d4e5f6
Revises: d3b2a1c9f2e7
Create Date: 2026-01-06 16:30:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "f1b2c3d4e5f6"
down_revision = "d3b2a1c9f2e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("asr_quotas", sa.Column("owner_user_id", sa.UUID(as_uuid=False)))
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
    op.execute(
        """
        INSERT INTO asr_quotas (
            provider,
            window_type,
            window_start,
            window_end,
            quota_seconds,
            used_seconds,
            status
        )
        SELECT provider, window_type, window_start, window_end, 0, 0, 'exhausted'
        FROM (
            SELECT
                p.provider,
                wt.window_type,
                CASE
                    WHEN wt.window_type = 'day' THEN date_trunc('day', now())
                    ELSE date_trunc('month', now())
                END AS window_start,
                CASE
                    WHEN wt.window_type = 'day' THEN date_trunc('day', now()) + interval '1 day' - interval '1 microsecond'
                    ELSE date_trunc('month', now()) + interval '1 month' - interval '1 microsecond'
                END AS window_end
            FROM (VALUES ('tencent'), ('aliyun'), ('volcengine')) AS p(provider)
            CROSS JOIN (VALUES ('day'), ('month')) AS wt(window_type)
        ) AS seed
        ON CONFLICT (provider, window_type, window_start, owner_user_id) DO NOTHING;
        """
    )


def downgrade() -> None:
    op.drop_index("uk_asr_quotas_global_window", table_name="asr_quotas")
    op.drop_constraint("uk_asr_quotas_provider_window", "asr_quotas", type_="unique")
    op.create_unique_constraint(
        "uk_asr_quotas_provider_window",
        "asr_quotas",
        ["provider", "window_type", "window_start"],
    )
    op.drop_column("asr_quotas", "owner_user_id")
