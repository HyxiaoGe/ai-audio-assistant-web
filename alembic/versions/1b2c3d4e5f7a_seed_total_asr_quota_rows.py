"""seed total asr quota rows

Revision ID: 1b2c3d4e5f7a
Revises: f1b2c3d4e5f6
Create Date: 2026-01-07 14:30:00.000000

"""

from alembic import op


revision = "1b2c3d4e5f7a"
down_revision = "f1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
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
        SELECT provider,
               'total',
               TIMESTAMPTZ '1970-01-01 00:00:00+00',
               TIMESTAMPTZ '2099-12-31 23:59:59.999999+00',
               0,
               0,
               'exhausted'
        FROM (VALUES ('tencent'), ('aliyun'), ('volcengine')) AS p(provider)
        WHERE NOT EXISTS (
            SELECT 1
            FROM asr_quotas q
            WHERE q.provider = p.provider
              AND q.window_type = 'total'
              AND q.window_start = TIMESTAMPTZ '1970-01-01 00:00:00+00'
              AND q.owner_user_id IS NULL
        );
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM asr_quotas
        WHERE window_type = 'total'
          AND window_start = TIMESTAMPTZ '1970-01-01 00:00:00+00'
          AND owner_user_id IS NULL;
        """
    )
