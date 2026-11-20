"""Add account-owned notifications for persisted workflow events.

Revision ID: 202611190002
Revises: 202611190001
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "202611190002"
down_revision: Union[str, None] = "202611190001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_notifications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("public_id", sa.String(length=32), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=True),
        sa.Column("source_event_key", sa.String(length=200), nullable=False),
        sa.Column("notification_type", sa.String(length=40), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("message", sa.String(length=400), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "notification_type IN ('workflow_decision', 'learning_pathways_ready')",
            name="ck_user_notifications_type",
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workflow_id", "owner_user_id"],
            ["career_workflows.id", "career_workflows.owner_user_id"],
            ondelete="CASCADE",
            name="fk_user_notifications_workflow_owner",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_user_id", "source_event_key", name="uq_user_notifications_owner_event"),
        sa.UniqueConstraint("public_id", name="uq_user_notifications_public_id"),
    )
    op.create_index("ix_user_notifications_owner_created", "user_notifications", ["owner_user_id", "created_at"])
    op.create_index("ix_user_notifications_owner_unread", "user_notifications", ["owner_user_id", "read_at"])


def downgrade() -> None:
    op.drop_index("ix_user_notifications_owner_unread", table_name="user_notifications")
    op.drop_index("ix_user_notifications_owner_created", table_name="user_notifications")
    op.drop_table("user_notifications")
