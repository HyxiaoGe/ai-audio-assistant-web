"""Add visual summary fields to summaries table

Revision ID: a1b2c3d4e5f6
Revises: f95331911f51
Create Date: 2026-01-17 12:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "a1b2c3d4e5f6"
down_revision = "f95331911f51"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add visual summary fields to summaries table
    op.add_column("summaries", sa.Column("visual_format", sa.String(length=20), nullable=True))
    op.add_column("summaries", sa.Column("visual_content", sa.Text(), nullable=True))
    op.add_column("summaries", sa.Column("image_key", sa.String(length=500), nullable=True))
    op.add_column("summaries", sa.Column("image_format", sa.String(length=10), nullable=True))


def downgrade() -> None:
    # Remove visual summary fields from summaries table
    op.drop_column("summaries", "image_format")
    op.drop_column("summaries", "image_key")
    op.drop_column("summaries", "visual_content")
    op.drop_column("summaries", "visual_format")
