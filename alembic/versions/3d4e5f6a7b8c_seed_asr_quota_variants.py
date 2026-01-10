"""seed asr quota variants

Revision ID: 3d4e5f6a7b8c
Revises: 2c4d5e6f7a8b
Create Date: 2026-01-08 18:40:00.000000

"""

from alembic import op


revision = "3d4e5f6a7b8c"
down_revision = "2c4d5e6f7a8b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO asr_quotas (
            provider,
            variant,
            window_type,
            window_start,
            window_end,
            quota_seconds,
            used_seconds,
            status,
            owner_user_id
        )
        SELECT
            provider,
            v.variant,
            window_type,
            window_start,
            window_end,
            quota_seconds,
            0,
            'active',
            owner_user_id
        FROM asr_quotas q
        CROSS JOIN (VALUES ('file_fast'), ('stream_async'), ('stream_realtime')) AS v(variant)
        WHERE q.variant = 'file'
          AND NOT EXISTS (
              SELECT 1
              FROM asr_quotas existing
              WHERE existing.provider = q.provider
                AND existing.variant = v.variant
                AND existing.window_type = q.window_type
                AND existing.window_start = q.window_start
                AND (
                    (existing.owner_user_id IS NULL AND q.owner_user_id IS NULL)
                    OR existing.owner_user_id = q.owner_user_id
                )
          );
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM asr_quotas
        WHERE variant IN ('file_fast', 'stream_async', 'stream_realtime');
        """
    )
