"""restore accounts table

Revision ID: 7b6a0f1d4c8f
Revises: aa2b7f8c1e23
Create Date: 2026-01-06 11:40:00.000000

"""
from alembic import op


revision = "7b6a0f1d4c8f"
down_revision = "aa2b7f8c1e23"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS accounts (
            id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
            user_id uuid NOT NULL,
            provider varchar(50) NOT NULL,
            provider_account_id varchar(255) NOT NULL,
            access_token text,
            refresh_token text,
            token_expires_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uk_accounts_provider UNIQUE (provider, provider_account_id),
            CONSTRAINT fk_accounts_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_accounts_user ON accounts (user_id);")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_accounts_user;")
    op.execute("DROP TABLE IF EXISTS accounts;")
