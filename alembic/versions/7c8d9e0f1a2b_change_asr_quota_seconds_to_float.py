"""change asr quota seconds to float

Revision ID: 7c8d9e0f1a2b
Revises: 1b2c3d4e5f7a
Create Date: 2026-01-07 15:10:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "7c8d9e0f1a2b"
down_revision = "1b2c3d4e5f7a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("asr_quotas", "quota_seconds", type_=sa.Float(), existing_type=sa.Integer())


def downgrade() -> None:
    op.alter_column("asr_quotas", "quota_seconds", type_=sa.Integer(), existing_type=sa.Float())
