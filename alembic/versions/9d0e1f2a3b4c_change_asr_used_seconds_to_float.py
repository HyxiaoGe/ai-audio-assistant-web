"""change asr used seconds to float

Revision ID: 9d0e1f2a3b4c
Revises: 7c8d9e0f1a2b
Create Date: 2026-01-07 15:40:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "9d0e1f2a3b4c"
down_revision = "7c8d9e0f1a2b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("asr_quotas", "used_seconds", type_=sa.Float(), existing_type=sa.Integer())


def downgrade() -> None:
    op.alter_column("asr_quotas", "used_seconds", type_=sa.Integer(), existing_type=sa.Float())
