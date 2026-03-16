"""unify user identity: replace user_ids, rename users -> user_profiles

Revision ID: c6d7e8f9a0b1
Revises: b5d8ec2b7fed
Create Date: 2026-03-16 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c6d7e8f9a0b1"
down_revision: Union[str, None] = "b5d8ec2b7fed"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# User ID mapping: old local ID → auth-service ID
USER_ID_MAP = {
    "5ce9a709-891b-4fcf-bfae-6da088a84dbc": "f6d3827e-3827-4c4c-8e5e-6880a1c05f22",  # seanfield767@gmail.com
    "ccdd4d22-9b6e-47ba-84a6-cc882e3b70a1": "ea1fd5a4-f7ab-4e30-82d8-bc006384bb56",  # hyxiao97@gmail.com
    "d8ed0a43-38c0-4194-8454-ae0305672a14": "4238d7e4-c647-4bc9-8ee4-9f26c75545f7",  # 18889592303@163.com
}

# Tables with user_id FK to users
FK_TABLES_CASCADE = [
    "accounts",
    "tasks",
    "youtube_subscriptions",
    "youtube_videos",
    "youtube_auto_transcribe_logs",
    "notifications",
    "asr_usages",
    "llm_usages",
    "rag_chunks",
]

FK_TABLES_SET_NULL = [
    ("service_configs", "owner_user_id"),
    ("service_configs", "updated_by"),
    ("service_config_history", "owner_user_id"),
    ("service_config_history", "updated_by"),
]


def upgrade() -> None:
    # ── Step 1: Drop all FK constraints pointing to users ──
    for table in FK_TABLES_CASCADE:
        op.drop_constraint(f"{table}_user_id_fkey", table, type_="foreignkey")

    for table, column in FK_TABLES_SET_NULL:
        op.drop_constraint(f"{table}_{column}_fkey", table, type_="foreignkey")

    # ── Step 2: Update user_ids in all referencing tables ──
    for old_id, new_id in USER_ID_MAP.items():
        for table in FK_TABLES_CASCADE:
            op.execute(
                f"UPDATE {table} SET user_id = '{new_id}' WHERE user_id = '{old_id}'"
            )
        for table, column in FK_TABLES_SET_NULL:
            op.execute(
                f"UPDATE {table} SET {column} = '{new_id}' WHERE {column} = '{old_id}'"
            )

    # ── Step 3: Update users table primary keys ──
    for old_id, new_id in USER_ID_MAP.items():
        op.execute(
            f"UPDATE users SET id = '{new_id}' WHERE id = '{old_id}'"
        )

    # ── Step 4: Drop identity columns from users ──
    op.drop_column("users", "email")
    op.drop_column("users", "name")
    op.drop_column("users", "avatar_url")
    op.drop_column("users", "phone")
    op.drop_column("users", "locale")
    op.drop_column("users", "timezone")

    # Drop related indexes (may fail if they don't exist, handled by IF EXISTS in raw SQL)
    op.execute("DROP INDEX IF EXISTS idx_users_email")
    op.execute("DROP INDEX IF EXISTS idx_users_phone")
    op.execute("DROP INDEX IF EXISTS ix_users_email")
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS uk_users_email")

    # ── Step 5: Rename settings → app_settings ──
    op.alter_column("users", "settings", new_column_name="app_settings")

    # ── Step 6: Rename table users → user_profiles ──
    op.rename_table("users", "user_profiles")

    # Rename the status index
    op.execute("ALTER INDEX IF EXISTS idx_users_status RENAME TO idx_user_profiles_status")

    # ── Step 7: Recreate FK constraints pointing to user_profiles ──
    for table in FK_TABLES_CASCADE:
        op.create_foreign_key(
            f"{table}_user_id_fkey",
            table,
            "user_profiles",
            ["user_id"],
            ["id"],
            ondelete="CASCADE",
        )

    for table, column in FK_TABLES_SET_NULL:
        op.create_foreign_key(
            f"{table}_{column}_fkey",
            table,
            "user_profiles",
            [column],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    # Reverse ID mapping
    REVERSE_MAP = {v: k for k, v in USER_ID_MAP.items()}

    # Drop FK constraints
    for table in FK_TABLES_CASCADE:
        op.drop_constraint(f"{table}_user_id_fkey", table, type_="foreignkey")
    for table, column in FK_TABLES_SET_NULL:
        op.drop_constraint(f"{table}_{column}_fkey", table, type_="foreignkey")

    # Rename table back
    op.rename_table("user_profiles", "users")

    # Rename column back
    op.alter_column("users", "app_settings", new_column_name="settings")

    # Restore dropped columns
    import sqlalchemy as sa
    op.add_column("users", sa.Column("email", sa.String(255), nullable=True))
    op.add_column("users", sa.Column("name", sa.String(255), nullable=True))
    op.add_column("users", sa.Column("avatar_url", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("phone", sa.String(20), nullable=True))
    op.add_column("users", sa.Column("locale", sa.String(10), server_default="zh", nullable=False))
    op.add_column("users", sa.Column("timezone", sa.String(50), server_default="Asia/Shanghai", nullable=False))

    # Reverse user IDs
    for new_id, old_id in REVERSE_MAP.items():
        op.execute(f"UPDATE users SET id = '{old_id}' WHERE id = '{new_id}'")
        for table in FK_TABLES_CASCADE:
            op.execute(f"UPDATE {table} SET user_id = '{old_id}' WHERE user_id = '{new_id}'")
        for table, column in FK_TABLES_SET_NULL:
            op.execute(f"UPDATE {table} SET {column} = '{old_id}' WHERE {column} = '{new_id}'")

    # Recreate FK constraints to users
    for table in FK_TABLES_CASCADE:
        op.create_foreign_key(
            f"{table}_user_id_fkey", table, "users", ["user_id"], ["id"], ondelete="CASCADE"
        )
    for table, column in FK_TABLES_SET_NULL:
        op.create_foreign_key(
            f"{table}_{column}_fkey", table, "users", [column], ["id"], ondelete="SET NULL"
        )
