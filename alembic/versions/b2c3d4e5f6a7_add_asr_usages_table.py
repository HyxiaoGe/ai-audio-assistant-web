"""Add ASR usages table for detailed call tracking

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-01-23 10:00:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "asr_usages",
        # Primary key
        sa.Column(
            "id",
            sa.UUID(as_uuid=False),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        # Foreign keys
        sa.Column("user_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("task_id", sa.UUID(as_uuid=False), nullable=True),
        # Provider info
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column(
            "variant",
            sa.String(length=30),
            server_default=sa.text("'file'"),
            nullable=False,
        ),
        sa.Column("external_task_id", sa.String(length=100), nullable=True),
        # Usage info
        sa.Column("duration_seconds", sa.Float(), nullable=False),
        sa.Column(
            "estimated_cost",
            sa.Float(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("actual_cost", sa.Float(), nullable=True),
        # Request info
        sa.Column("audio_url", sa.String(length=1000), nullable=True),
        sa.Column("audio_format", sa.String(length=20), nullable=True),
        # Status
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'success'"),
            nullable=False,
        ),
        sa.Column("error_code", sa.String(length=50), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        # Performance
        sa.Column("processing_time_ms", sa.Integer(), nullable=True),
        # JSONB fields
        sa.Column(
            "request_params",
            JSONB,
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "response_metadata",
            JSONB,
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        # Reconciliation
        sa.Column(
            "reconciled",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=True),
        # Timestamps
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
        # Constraints
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["tasks.id"],
            ondelete="SET NULL",
        ),
    )

    # Create indexes for common queries
    op.create_index("idx_asr_usages_user", "asr_usages", ["user_id"])
    op.create_index("idx_asr_usages_task", "asr_usages", ["task_id"])
    op.create_index("idx_asr_usages_provider", "asr_usages", ["provider"])
    op.create_index("idx_asr_usages_created_at", "asr_usages", ["created_at"])
    op.create_index(
        "idx_asr_usages_user_provider_created",
        "asr_usages",
        ["user_id", "provider", "created_at"],
    )
    op.create_index(
        "idx_asr_usages_billing",
        "asr_usages",
        ["provider", "external_task_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_asr_usages_billing", table_name="asr_usages")
    op.drop_index("idx_asr_usages_user_provider_created", table_name="asr_usages")
    op.drop_index("idx_asr_usages_created_at", table_name="asr_usages")
    op.drop_index("idx_asr_usages_provider", table_name="asr_usages")
    op.drop_index("idx_asr_usages_task", table_name="asr_usages")
    op.drop_index("idx_asr_usages_user", table_name="asr_usages")
    op.drop_table("asr_usages")
