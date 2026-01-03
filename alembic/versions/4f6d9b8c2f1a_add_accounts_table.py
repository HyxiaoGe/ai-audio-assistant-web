"""add accounts table

Revision ID: 4f6d9b8c2f1a
Revises: 30ade5c26128
Create Date: 2025-01-09 10:15:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "4f6d9b8c2f1a"
down_revision = "30ade5c26128"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column(
            "id",
            sa.UUID(as_uuid=False),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.UUID(as_uuid=False),
            nullable=False,
        ),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("provider_account_id", sa.String(length=255), nullable=False),
        sa.Column("access_token", sa.Text(), nullable=True),
        sa.Column("refresh_token", sa.Text(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider",
            "provider_account_id",
            name="uk_accounts_provider",
        ),
    )
    op.create_index("idx_accounts_user", "accounts", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_accounts_user", table_name="accounts")
    op.drop_table("accounts")
