"""merge asr pricing with existing migrations

Revision ID: 6bb23b8bc4d0
Revises: 7e8a9a99ea5e, e5f6a7b8c9d0
Create Date: 2026-01-24 00:35:16.702923

"""
from alembic import op
import sqlalchemy as sa



revision = '6bb23b8bc4d0'
down_revision = ('7e8a9a99ea5e', 'e5f6a7b8c9d0')
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
