"""Add saved standalone Bias Audits and current candidate feedback.

Revision ID: 202611190001
Revises: 202610310001
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "202611190001"
down_revision: Union[str, None] = "202610310001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_json_document = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "candidate_experience_feedback",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column("consent_to_aggregate", sa.Boolean(), nullable=False),
        sa.Column("application_experience", sa.Integer(), nullable=False),
        sa.Column("process_accessibility", sa.Integer(), nullable=False),
        sa.Column("job_requirements_clarity", sa.Integer(), nullable=False),
        sa.Column("perceived_fairness", sa.Integer(), nullable=False),
        sa.Column("suggestion_categories", _json_document, server_default=sa.text("'[]'"), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("application_experience BETWEEN 1 AND 5", name="ck_candidate_experience_application_rating"),
        sa.CheckConstraint("process_accessibility BETWEEN 1 AND 5", name="ck_candidate_experience_accessibility_rating"),
        sa.CheckConstraint("job_requirements_clarity BETWEEN 1 AND 5", name="ck_candidate_experience_requirements_rating"),
        sa.CheckConstraint("perceived_fairness BETWEEN 1 AND 5", name="ck_candidate_experience_fairness_rating"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_user_id", "submission_id", name="uq_candidate_experience_owner_submission"),
    )
    op.create_index("ix_candidate_experience_owner_submitted", "candidate_experience_feedback", ["owner_user_id", "submitted_at"])

    op.create_table(
        "bias_audits",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("public_id", sa.String(length=32), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("job_title", sa.String(length=160), nullable=True),
        sa.Column("result_snapshot", _json_document, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "owner_user_id", name="uq_bias_audits_id_owner"),
        sa.UniqueConstraint("public_id", name="uq_bias_audits_public_id"),
    )
    op.create_index("ix_bias_audits_owner_created", "bias_audits", ["owner_user_id", "created_at"])

    op.create_table(
        "bias_audit_review_selections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("audit_id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("finding_index", sa.Integer(), nullable=False),
        sa.Column("review_state", sa.String(length=20), server_default="needs_review", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("finding_index >= 0", name="ck_bias_audit_review_finding_index"),
        sa.CheckConstraint("review_state IN ('needs_review', 'accepted', 'edited', 'dismissed')", name="ck_bias_audit_review_state"),
        sa.ForeignKeyConstraint(
            ["audit_id", "owner_user_id"],
            ["bias_audits.id", "bias_audits.owner_user_id"],
            ondelete="CASCADE",
            name="fk_bias_audit_review_selections_audit_owner",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("audit_id", "finding_index", name="uq_bias_audit_review_finding"),
    )
    op.create_index("ix_bias_audit_review_owner_audit", "bias_audit_review_selections", ["owner_user_id", "audit_id"])


def downgrade() -> None:
    op.drop_index("ix_bias_audit_review_owner_audit", table_name="bias_audit_review_selections")
    op.drop_table("bias_audit_review_selections")
    op.drop_index("ix_bias_audits_owner_created", table_name="bias_audits")
    op.drop_table("bias_audits")
    op.drop_index("ix_candidate_experience_owner_submitted", table_name="candidate_experience_feedback")
    op.drop_table("candidate_experience_feedback")
