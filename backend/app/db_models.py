"""Initial PostgreSQL persistence schema; no resume files or raw resume text."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


_JSON_DOCUMENT = JSON().with_variant(JSONB, "postgresql")


class Base(DeclarativeBase):
    """Declarative metadata for Alembic and future repository services."""


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("email_normalized", name="uq_users_email_normalized"),
        CheckConstraint("length(email_normalized) BETWEEN 3 AND 320", name="ck_users_email_length"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    email_normalized: Mapped[str] = mapped_column(String(320), nullable=False)
    # Argon2id encoded hash; plaintext passwords are never stored.
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class UserSession(Base):
    __tablename__ = "user_sessions"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_user_sessions_token_hash"),
        Index("ix_user_sessions_user_expiry", "user_id", "absolute_expires_at"),
        CheckConstraint("length(token_hash) = 64", name="ck_user_sessions_token_hash_length"),
        CheckConstraint("length(csrf_token_hash) = 64", name="ck_user_sessions_csrf_hash_length"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    # SHA-256 digests only; raw cookie and CSRF tokens are never stored.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    csrf_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    idle_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    absolute_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ResumeAnalysis(Base):
    __tablename__ = "resume_analyses"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_resume_analyses_public_id"),
        UniqueConstraint("id", "owner_user_id", name="uq_resume_analyses_id_owner"),
        Index("ix_resume_analyses_owner_created", "owner_user_id", "created_at"),
        CheckConstraint("analysis_mode IN ('ai', 'local', 'development')", name="ck_resume_analyses_mode"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    public_id: Mapped[str] = mapped_column(String(128), nullable=False)
    owner_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    analysis_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AnalysisSkill(Base):
    __tablename__ = "analysis_skills"
    __table_args__ = (
        ForeignKeyConstraint(
            ["analysis_id", "owner_user_id"],
            ["resume_analyses.id", "resume_analyses.owner_user_id"],
            ondelete="CASCADE",
            name="fk_analysis_skills_analysis_owner",
        ),
        Index("ix_analysis_skills_owner_analysis", "owner_user_id", "analysis_id"),
        Index("ix_analysis_skills_analysis_name", "analysis_id", "skill_name"),
        CheckConstraint("evidence_type IN ('explicit', 'inferred')", name="ck_analysis_skills_evidence_type"),
        CheckConstraint("detection_method IN ('ai', 'local')", name="ck_analysis_skills_detection_method"),
        CheckConstraint("validation_status IN ('evidence_found', 'inference_unverified')", name="ck_analysis_skills_validation_status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    analysis_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    owner_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    skill_name: Mapped[str] = mapped_column(String(100), nullable=False)
    evidence_type: Mapped[str] = mapped_column(String(16), nullable=False)
    detection_method: Mapped[str] = mapped_column(String(16), nullable=False)
    validation_status: Mapped[str] = mapped_column(String(32), nullable=False)
    supporting_excerpt: Mapped[str] = mapped_column(String(600), nullable=False)
    relevance_explanation: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class CareerWorkflow(Base):
    __tablename__ = "career_workflows"
    __table_args__ = (
        ForeignKeyConstraint(
            ["analysis_id", "owner_user_id"],
            ["resume_analyses.id", "resume_analyses.owner_user_id"],
            ondelete="CASCADE",
            name="fk_career_workflows_analysis_owner",
        ),
        UniqueConstraint("id", "owner_user_id", name="uq_career_workflows_id_owner"),
        UniqueConstraint("public_id", name="uq_career_workflows_public_id"),
        Index("ix_career_workflows_owner_created", "owner_user_id", "created_at"),
        CheckConstraint("workflow_status IN ('awaiting_human_review', 'revise_required', 'stopped', 'complete')", name="ck_career_workflows_status"),
        CheckConstraint("version >= 1", name="ck_career_workflows_version"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    public_id: Mapped[str] = mapped_column(String(128), nullable=False)
    owner_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    analysis_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    job_title: Mapped[str] = mapped_column(String(160), nullable=False)
    job_description: Mapped[str] = mapped_column(String(10_000), nullable=False)
    geography: Mapped[str | None] = mapped_column(String(120))
    essential_skills: Mapped[list[str]] = mapped_column(_JSON_DOCUMENT, nullable=False)
    preferred_skills: Mapped[list[str]] = mapped_column(_JSON_DOCUMENT, nullable=False)
    workflow_status: Mapped[str] = mapped_column(String(32), nullable=False)
    reviewable_requirement_ids: Mapped[list[str]] = mapped_column(_JSON_DOCUMENT, nullable=False)
    stages_snapshot: Mapped[list[dict[str, object]]] = mapped_column(_JSON_DOCUMENT, nullable=False)
    skill_passport_snapshot: Mapped[dict[str, object]] = mapped_column(_JSON_DOCUMENT, nullable=False)
    market_intelligence_snapshot: Mapped[dict[str, object]] = mapped_column(_JSON_DOCUMENT, nullable=False)
    job_audit_snapshot: Mapped[dict[str, object]] = mapped_column(_JSON_DOCUMENT, nullable=False)
    inclusive_matching_snapshot: Mapped[dict[str, object]] = mapped_column(_JSON_DOCUMENT, nullable=False)
    learning_pathways_snapshot: Mapped[dict[str, object]] = mapped_column(_JSON_DOCUMENT, nullable=False)
    workflow_summary: Mapped[str] = mapped_column(String(2_000), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkflowReview(Base):
    __tablename__ = "workflow_reviews"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workflow_id", "owner_user_id"],
            ["career_workflows.id", "career_workflows.owner_user_id"],
            ondelete="CASCADE",
            name="fk_workflow_reviews_workflow_owner",
        ),
        UniqueConstraint("workflow_id", name="uq_workflow_reviews_workflow"),
        UniqueConstraint("id", "owner_user_id", name="uq_workflow_reviews_id_owner"),
        CheckConstraint("decision IS NULL OR decision IN ('proceed', 'revise', 'stop')", name="ck_workflow_reviews_decision"),
        CheckConstraint("(decision IS NULL AND review_completed_at IS NULL) OR (decision IS NOT NULL AND review_completed_at IS NOT NULL)", name="ck_workflow_reviews_completion_state"),
        Index("ix_workflow_reviews_owner_completed", "owner_user_id", "review_completed_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    workflow_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    owner_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    decision: Mapped[str | None] = mapped_column(String(16))
    reviewed_requirement_ids: Mapped[list[str]] = mapped_column(_JSON_DOCUMENT, nullable=False)
    review_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    # Notes and requester-supplied free text are intentionally not stored.


class ImpactObservation(Base):
    __tablename__ = "impact_observations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workflow_review_id", "owner_user_id"],
            ["workflow_reviews.id", "workflow_reviews.owner_user_id"],
            ondelete="CASCADE",
            name="fk_impact_observations_review_owner",
        ),
        UniqueConstraint("owner_user_id", "submission_id", name="uq_impact_observations_owner_submission"),
        CheckConstraint("observation_type IN ('review_duration', 'baseline_duration')", name="ck_impact_observations_type"),
        CheckConstraint("numeric_value > 0", name="ck_impact_observations_positive_value"),
        Index("ix_impact_observations_owner_created", "owner_user_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    workflow_review_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    submission_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    observation_type: Mapped[str] = mapped_column(String(24), nullable=False)
    numeric_value: Mapped[float] = mapped_column(Numeric(12, 3), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RecruiterFeedback(Base):
    __tablename__ = "recruiter_feedback"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workflow_id", "owner_user_id"],
            ["career_workflows.id", "career_workflows.owner_user_id"],
            ondelete="CASCADE",
            name="fk_recruiter_feedback_workflow_owner",
        ),
        UniqueConstraint("owner_user_id", "submission_id", name="uq_recruiter_feedback_owner_submission"),
        CheckConstraint("evidence_usefulness BETWEEN 1 AND 5", name="ck_recruiter_feedback_evidence_rating"),
        CheckConstraint("explanation_clarity BETWEEN 1 AND 5", name="ck_recruiter_feedback_clarity_rating"),
        CheckConstraint("review_ease BETWEEN 1 AND 5", name="ck_recruiter_feedback_ease_rating"),
        Index("ix_recruiter_feedback_owner_submitted", "owner_user_id", "submitted_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    workflow_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    submission_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    consent_to_aggregate: Mapped[bool] = mapped_column(Boolean, nullable=False)
    evidence_usefulness: Mapped[int] = mapped_column(Integer, nullable=False)
    explanation_clarity: Mapped[int] = mapped_column(Integer, nullable=False)
    review_ease: Mapped[int] = mapped_column(Integer, nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Free-text feedback is intentionally not stored.


class CandidateFeedback(Base):
    __tablename__ = "candidate_feedback"
    __table_args__ = (
        ForeignKeyConstraint(
            ["analysis_id", "owner_user_id"],
            ["resume_analyses.id", "resume_analyses.owner_user_id"],
            ondelete="CASCADE",
            name="fk_candidate_feedback_analysis_owner",
        ),
        UniqueConstraint("owner_user_id", "submission_id", name="uq_candidate_feedback_owner_submission"),
        CheckConstraint("skill_representation_clarity BETWEEN 1 AND 5", name="ck_candidate_feedback_representation_rating"),
        CheckConstraint("understanding_of_skills BETWEEN 1 AND 5", name="ck_candidate_feedback_understanding_rating"),
        CheckConstraint("perceived_transparency BETWEEN 1 AND 5", name="ck_candidate_feedback_transparency_rating"),
        CheckConstraint("overall_experience BETWEEN 1 AND 5", name="ck_candidate_feedback_experience_rating"),
        Index("ix_candidate_feedback_owner_submitted", "owner_user_id", "submitted_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    analysis_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    submission_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    consent_to_aggregate: Mapped[bool] = mapped_column(Boolean, nullable=False)
    skill_representation_clarity: Mapped[int] = mapped_column(Integer, nullable=False)
    understanding_of_skills: Mapped[int] = mapped_column(Integer, nullable=False)
    perceived_transparency: Mapped[int] = mapped_column(Integer, nullable=False)
    overall_experience: Mapped[int] = mapped_column(Integer, nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Free-text feedback is intentionally not stored.
