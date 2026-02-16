"""add prompt template library tables

Revision ID: a4c7cb1a6dbc
Revises: d5e6f7a8b9c0
Create Date: 2026-02-15 20:39:36.857262

"""
from alembic import op
import sqlalchemy as sa

from sqlalchemy.dialects import postgresql

revision = 'a4c7cb1a6dbc'
down_revision = 'd5e6f7a8b9c0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('prompt_templates',
    sa.Column('prompt_text', sa.Text(), nullable=False),
    sa.Column('display_name_en', sa.String(length=200), nullable=False),
    sa.Column('display_name_zh', sa.String(length=200), nullable=False),
    sa.Column('description_en', sa.Text(), nullable=True),
    sa.Column('description_zh', sa.Text(), nullable=True),
    sa.Column('preview_image_url', sa.String(length=500), nullable=True),
    sa.Column('category', sa.String(length=50), nullable=False),
    sa.Column('tags', postgresql.ARRAY(sa.String()), server_default=sa.text("'{}'::varchar[]"), nullable=False),
    sa.Column('style_keywords', postgresql.ARRAY(sa.String()), server_default=sa.text("'{}'::varchar[]"), nullable=False),
    sa.Column('parameters', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('difficulty', sa.String(length=20), nullable=False),
    sa.Column('language', sa.String(length=10), nullable=False),
    sa.Column('source', sa.String(length=20), nullable=False),
    sa.Column('use_count', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('like_count', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('favorite_count', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('trending_score', sa.Float(), server_default=sa.text('0.0'), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('created_by', sa.UUID(as_uuid=False), nullable=True),
    sa.Column('id', sa.UUID(as_uuid=False), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_prompt_templates_category', 'prompt_templates', ['category'], unique=False, postgresql_where=sa.text('deleted_at IS NULL'))
    op.create_index('ix_prompt_templates_tags', 'prompt_templates', ['tags'], unique=False, postgresql_using='gin')
    op.create_index('ix_prompt_templates_trending', 'prompt_templates', ['trending_score'], unique=False, postgresql_where=sa.text('deleted_at IS NULL AND is_active = TRUE'))
    op.create_index('ix_prompt_templates_use_count', 'prompt_templates', ['use_count'], unique=False, postgresql_where=sa.text('deleted_at IS NULL AND is_active = TRUE'))
    op.create_table('user_template_favorites',
    sa.Column('user_id', sa.UUID(as_uuid=False), nullable=False),
    sa.Column('template_id', sa.UUID(as_uuid=False), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['template_id'], ['prompt_templates.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', 'template_id')
    )
    op.create_table('user_template_likes',
    sa.Column('user_id', sa.UUID(as_uuid=False), nullable=False),
    sa.Column('template_id', sa.UUID(as_uuid=False), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['template_id'], ['prompt_templates.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', 'template_id')
    )
    op.create_table('user_template_usages',
    sa.Column('user_id', sa.UUID(as_uuid=False), nullable=True),
    sa.Column('template_id', sa.UUID(as_uuid=False), nullable=False),
    sa.Column('used_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.UUID(as_uuid=False), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['template_id'], ['prompt_templates.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_user_template_usages_template_id', 'user_template_usages', ['template_id'], unique=False)
    op.create_index('ix_user_template_usages_user_id', 'user_template_usages', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_user_template_usages_user_id', table_name='user_template_usages')
    op.drop_index('ix_user_template_usages_template_id', table_name='user_template_usages')
    op.drop_table('user_template_usages')
    op.drop_table('user_template_likes')
    op.drop_table('user_template_favorites')
    op.drop_index('ix_prompt_templates_use_count', table_name='prompt_templates', postgresql_where=sa.text('deleted_at IS NULL AND is_active = TRUE'))
    op.drop_index('ix_prompt_templates_trending', table_name='prompt_templates', postgresql_where=sa.text('deleted_at IS NULL AND is_active = TRUE'))
    op.drop_index('ix_prompt_templates_tags', table_name='prompt_templates', postgresql_using='gin')
    op.drop_index('ix_prompt_templates_category', table_name='prompt_templates', postgresql_where=sa.text('deleted_at IS NULL'))
    op.drop_table('prompt_templates')
