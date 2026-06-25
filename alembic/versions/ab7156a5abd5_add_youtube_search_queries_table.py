"""add youtube_search_queries table

Revision ID: ab7156a5abd5
Revises: c1d2e3f4a5b6
Create Date: 2026-06-26 00:59:41.063787

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = 'ab7156a5abd5'
down_revision = 'c1d2e3f4a5b6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "youtube_search_queries",
        sa.Column("id", postgresql.UUID(as_uuid=False), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("normalized_query", sa.String(length=128), nullable=False),
        sa.Column("display_query", sa.String(length=128), nullable=False),
        sa.Column("results_json", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("search_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_searched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_blocked", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("normalized_query", name="uk_youtube_search_queries_normalized"),
    )
    op.create_index(
        "idx_youtube_search_queries_trending",
        "youtube_search_queries",
        [sa.text("last_searched_at DESC")],
        postgresql_where=sa.text("is_blocked = false"),
    )


def downgrade() -> None:
    op.drop_index("idx_youtube_search_queries_trending", table_name="youtube_search_queries")
    op.drop_table("youtube_search_queries")
