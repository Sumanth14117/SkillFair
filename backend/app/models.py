from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class AuthRegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=12, max_length=128, repr=False)


class AuthLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=128, repr=False)


class AuthenticatedUserResponse(BaseModel):
    id: UUID
    email: str
    created_at: datetime


def _ensure_evidence_status(evidence_type: str, validation_status: str) -> None:
    expected_status = {
        "explicit": "evidence_found",
        "inferred": "inference_unverified",
    }.get(evidence_type)
    if expected_status is None or validation_status != expected_status:
        raise ValueError("evidence_type and validation_status are inconsistent")


class SkillEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_name: str = Field(min_length=1, max_length=100)
    evidence_type: Literal["explicit", "inferred"]
    detection_method: Literal["ai", "local"] = "ai"
    supporting_excerpt: str = Field(min_length=1, max_length=600)
    relevance_explanation: str = Field(min_length=1, max_length=500)
    validation_status: Literal["evidence_found", "inference_unverified"]

    @model_validator(mode="after")
    def validate_evidence_status(self) -> "SkillEvidence":
        _ensure_evidence_status(self.evidence_type, self.validation_status)
        if self.detection_method == "local" and self.evidence_type != "explicit":
            raise ValueError("local deterministic detection cannot produce inferred skills")
        return self


class SkillsDiscoverResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis_mode: Literal["development", "local", "ai"]
    message: str
    extracted_characters: int = Field(ge=0)
    skills: list[SkillEvidence] = Field(max_length=60)
    # Set by the API after storing the validated result; optional for direct analysis calls.
    analysis_id: str | None = None


class PersistedAnalysisResponse(BaseModel):
    """Owner-scoped latest analysis without original resume text or files."""

    model_config = ConfigDict(extra="forbid")

    analysis_id: str
    analysis_mode: Literal["development", "local", "ai"]
    created_at: datetime
    skills: list[SkillEvidence] = Field(max_length=60)


class SkillsProviderStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["none", "gemini", "groq"]
    configured: bool
    message: str


class JobMatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis_id: str = Field(min_length=16, max_length=128)
    job_title: str = Field(min_length=1, max_length=160)
    job_description: str = Field(min_length=1, max_length=10_000)
    required_skills: list[str] = Field(min_length=1, max_length=60)

    @field_validator("job_title", "job_description")
    @classmethod
    def require_non_whitespace_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("required_skills")
    @classmethod
    def require_nonblank_skills(cls, value: list[str]) -> list[str]:
        cleaned = [skill.strip() for skill in value]
        if not cleaned or any(not skill for skill in cleaned):
            raise ValueError("required skills must not be blank")
        return cleaned


class SkillMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    required_skill: str
    candidate_skill: str
    evidence_type: Literal["explicit", "inferred"]
    detection_method: Literal["ai", "local"] = "ai"
    validation_status: Literal["evidence_found", "inference_unverified"]
    supporting_excerpt: str
    relevance_explanation: str

    @model_validator(mode="after")
    def validate_evidence_status(self) -> "SkillMatch":
        _ensure_evidence_status(self.evidence_type, self.validation_status)
        if self.detection_method == "local" and self.evidence_type != "explicit":
            raise ValueError("local deterministic detection cannot produce inferred matches")
        return self


class JobMatchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_title: str
    total_required: int
    explicitly_supported: int
    inferred_unverified: int
    missing_count: int
    evidence_coverage_percent: int
    matched_skills: list[SkillMatch]
    missing_skills: list[str]
    calculation_method: str


class InclusiveMatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis_id: str = Field(min_length=16, max_length=128)
    job_title: str = Field(min_length=1, max_length=160)
    job_description: str = Field(min_length=1, max_length=10_000)
    essential_skills: list[str] = Field(default_factory=list, max_length=60)
    preferred_skills: list[str] = Field(default_factory=list, max_length=60)

    @field_validator("analysis_id")
    @classmethod
    def validate_inclusive_analysis_id(cls, value: str) -> str:
        value = value.strip()
        if not 16 <= len(value) <= 128:
            raise ValueError("analysis identifier must contain 16 to 128 non-whitespace characters")
        return value

    @field_validator("job_title", "job_description")
    @classmethod
    def validate_inclusive_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("essential_skills", "preferred_skills")
    @classmethod
    def validate_inclusive_skills(cls, value: list[str]) -> list[str]:
        cleaned = [skill.strip() for skill in value]
        if any(not skill for skill in cleaned):
            raise ValueError("skills must not be blank")
        if any(len(skill) > 100 for skill in cleaned):
            raise ValueError("each skill must be 100 characters or fewer")
        return cleaned

    @model_validator(mode="after")
    def limit_combined_inclusive_requirements(self) -> "InclusiveMatchRequest":
        if len(self.essential_skills) + len(self.preferred_skills) > 60:
            raise ValueError("essential and preferred skills combined must contain 60 or fewer items")
        return self


class LearningPathwaysRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis_id: str | None = Field(default=None, min_length=16, max_length=128)
    job_title: str = Field(min_length=1, max_length=160)
    required_skills: list[str] = Field(min_length=1, max_length=60)

    @field_validator("analysis_id")
    @classmethod
    def validate_analysis_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not 16 <= len(value) <= 128:
            raise ValueError("analysis identifier must contain 16 to 128 non-whitespace characters")
        return value

    @field_validator("job_title")
    @classmethod
    def require_nonblank_job_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("job title must not be blank")
        return value

    @field_validator("required_skills")
    @classmethod
    def require_nonblank_required_skills(cls, value: list[str]) -> list[str]:
        cleaned = [skill.strip() for skill in value]
        if not cleaned or any(not skill for skill in cleaned):
            raise ValueError("required skills must not be blank")
        if any(len(skill) > 100 for skill in cleaned):
            raise ValueError("each required skill must be 100 characters or fewer")
        return cleaned


LearningEvidenceStatus = Literal[
    "explicitly_evidenced",
    "inferred_unverified",
    "not_evidenced_in_analysis",
    "not_compared",
]


class LearningActivity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    activity_type: Literal["practice_exercise", "project_prompt", "demonstration"]
    title: str
    description: str
    relevance: str


class LearningPathwayItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    required_skill: str
    evidence_status: LearningEvidenceStatus
    evidenced_skill: str | None = None
    evidence_type: Literal["explicit", "inferred"] | None = None
    validation_status: Literal["evidence_found", "inference_unverified"] | None = None
    detection_method: Literal["ai", "local"] | None = None
    activities: list[LearningActivity]


class LearningPathwaysResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_title: str
    analysis_linked: bool
    requirement_count: int = Field(ge=0)
    pathways: list[LearningPathwayItem]
    disclaimer: str


class TransferableSkillsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis_id: str | None = Field(default=None, min_length=16, max_length=128)
    source_role: str = Field(min_length=1, max_length=160)
    target_role: str = Field(min_length=1, max_length=160)
    target_requirements: list[str] = Field(default_factory=list, max_length=60)

    @field_validator("analysis_id")
    @classmethod
    def validate_transfer_analysis_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not 16 <= len(value) <= 128:
            raise ValueError("analysis identifier must contain 16 to 128 non-whitespace characters")
        return value

    @field_validator("source_role", "target_role")
    @classmethod
    def require_nonblank_transfer_roles(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("role names must not be blank")
        return value

    @field_validator("target_requirements")
    @classmethod
    def validate_transfer_requirements(cls, value: list[str]) -> list[str]:
        cleaned = [requirement.strip() for requirement in value]
        if any(not requirement for requirement in cleaned):
            raise ValueError("target requirements must not be blank")
        if any(len(requirement) > 100 for requirement in cleaned):
            raise ValueError("each target requirement must be 100 characters or fewer")
        return cleaned


class TransferabilityEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_name: str
    evidence_type: Literal["explicit", "inferred"]
    validation_status: Literal["evidence_found", "inference_unverified"]
    detection_method: Literal["ai", "local"] = "ai"
    supporting_excerpt: str
    relationship_type: Literal["exact_name_match", "curated_adjacent_capability"]
    explanation: str

    @model_validator(mode="after")
    def validate_transfer_evidence_status(self) -> "TransferabilityEvidence":
        _ensure_evidence_status(self.evidence_type, self.validation_status)
        if self.detection_method == "local" and self.evidence_type != "explicit":
            raise ValueError("local deterministic detection cannot produce inferred transfer evidence")
        return self


class TransferabilityResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_requirement: str
    status: Literal["exact_match", "potential_transfer", "not_evidenced", "not_compared"]
    candidate_evidence: list[TransferabilityEvidence]
    explanation: str
    exploration_prompt: str | None = None

    @model_validator(mode="after")
    def validate_result_evidence(self) -> "TransferabilityResult":
        supported = self.status in {"exact_match", "potential_transfer"}
        if supported != bool(self.candidate_evidence):
            raise ValueError("transferability status and candidate evidence are inconsistent")
        expected_relationship = "exact_name_match" if self.status == "exact_match" else "curated_adjacent_capability"
        if supported and any(item.relationship_type != expected_relationship for item in self.candidate_evidence):
            raise ValueError("candidate evidence relationship type does not match transferability status")
        if self.status in {"not_evidenced", "not_compared"} and self.exploration_prompt is None:
            raise ValueError("unresolved requirements need an exploration prompt")
        return self


class TransferableSkillsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_role: str
    target_role: str
    analysis_linked: bool
    candidate_comparison_status: Literal["compared", "not_compared"]
    results: list[TransferabilityResult]
    exploration_prompts: list[str]
    disclaimer: str


class MarketIntelligenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_title: str = Field(min_length=1, max_length=160)
    geography: str | None = Field(default=None, min_length=1, max_length=120)

    @field_validator("job_title", "geography")
    @classmethod
    def strip_and_validate_market_request_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class MarketDataSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=1, max_length=120)
    source_name: str = Field(min_length=1, max_length=200)
    source_type: str = Field(min_length=1, max_length=80)
    verification_status: Literal["verified"]
    retrieved_at: datetime
    published_at: datetime | None = None
    geography: str | None = Field(default=None, max_length=120)
    source_reference: str = Field(min_length=1, max_length=500)

    @field_validator("source_id", "source_name", "source_type", "source_reference")
    @classmethod
    def strip_required_source_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("source provenance fields must not be blank")
        return value

    @model_validator(mode="after")
    def require_timezone_aware_provenance(self) -> "MarketDataSource":
        timestamps = [self.retrieved_at]
        if self.published_at is not None:
            timestamps.append(self.published_at)
        if any(timestamp.tzinfo is None or timestamp.utcoffset() is None for timestamp in timestamps):
            raise ValueError("market source timestamps must include a timezone")
        return self


class MarketInsight(BaseModel):
    model_config = ConfigDict(extra="forbid")

    insight_id: str = Field(min_length=1, max_length=120)
    category: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=1000)
    value: float | None = Field(default=None, allow_inf_nan=False)
    unit: str | None = Field(default=None, max_length=80)
    period: str | None = Field(default=None, max_length=120)
    geography: str | None = Field(default=None, max_length=120)
    source_id: str = Field(min_length=1, max_length=120)


class MarketDataSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sources: list[MarketDataSource]
    insights: list[MarketInsight]


class MarketIntelligenceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_title: str
    geography: str | None
    data_availability: Literal["available", "unavailable"]
    sources: list[MarketDataSource]
    insights: list[MarketInsight]
    message: str

    @model_validator(mode="after")
    def validate_market_provenance(self) -> "MarketIntelligenceResponse":
        source_ids = {source.source_id for source in self.sources}
        if self.data_availability == "available" and (not source_ids or not self.insights):
            raise ValueError("available market data requires at least one verified source and insight")
        if self.data_availability == "unavailable" and self.insights:
            raise ValueError("unavailable market data cannot include insights")
        if any(insight.source_id not in source_ids for insight in self.insights):
            raise ValueError("each market insight must reference a response source")
        return self


AuditImportance = Literal["essential", "preferred", "unspecified"]


class AuditCriterion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=1000)
    importance: AuditImportance = "unspecified"

    @field_validator("text")
    @classmethod
    def require_non_whitespace_criterion(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("criterion must not be blank")
        return value


class BiasAuditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_title: str | None = Field(default=None, max_length=160)
    job_description: str = Field(default="", max_length=10_000)
    requirements: list[AuditCriterion] = Field(default_factory=list, max_length=60)

    @field_validator("job_title", "job_description")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def require_audit_input(self) -> "BiasAuditRequest":
        if not self.job_description and not self.requirements:
            raise ValueError("provide a job description or at least one requirement")
        return self


class BiasAuditFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str
    category: Literal[
        "credential_requirement",
        "potentially_exclusionary_wording",
        "vague_requirement",
        "experience_threshold",
    ]
    priority: Literal["low", "medium", "high"]
    triggering_phrase: str
    criterion_importance: AuditImportance
    explanation: str
    suggested_revision_or_action: str
    status: Literal["potential_concern_for_human_review"] = "potential_concern_for_human_review"
    human_review_required: Literal[True] = True


InclusiveRequirementStatus = Literal[
    "explicitly_evidenced",
    "inferred_unverified",
    "potential_transfer",
    "not_evidenced",
]


class InclusiveMatchRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill: str
    importance: Literal["essential", "preferred"]
    status: InclusiveRequirementStatus
    evidence: list[TransferabilityEvidence] = Field(default_factory=list)
    explanation: str

    @model_validator(mode="after")
    def validate_inclusive_requirement_evidence(self) -> "InclusiveMatchRequirement":
        supported = self.status != "not_evidenced"
        if supported != bool(self.evidence):
            raise ValueError("inclusive match status and evidence are inconsistent")
        if self.status == "explicitly_evidenced" and any(
            item.evidence_type != "explicit" or item.relationship_type != "exact_name_match" for item in self.evidence
        ):
            raise ValueError("explicit evidence status requires exact explicit evidence")
        if self.status == "inferred_unverified" and any(
            item.evidence_type != "inferred" or item.relationship_type != "exact_name_match" for item in self.evidence
        ):
            raise ValueError("inferred status requires exact inferred evidence")
        if self.status == "potential_transfer" and any(
            item.relationship_type != "curated_adjacent_capability" for item in self.evidence
        ):
            raise ValueError("potential transfer status requires curated adjacent evidence")
        return self


class InclusiveMatchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_title: str
    requirements: list[InclusiveMatchRequirement]
    exact_explicit_count: int = Field(ge=0)
    exact_inferred_count: int = Field(ge=0)
    potential_transfer_count: int = Field(ge=0)
    not_evidenced_count: int = Field(ge=0)
    bias_audit_findings: list[BiasAuditFinding] = Field(default_factory=list)
    human_review_prompts: list[str]
    human_review_required: Literal[True] = True
    disclaimer: str


class BiasAuditResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_title: str | None
    criteria_reviewed: int = Field(ge=0)
    finding_count: int = Field(ge=0)
    findings: list[BiasAuditFinding]
    human_review_required: Literal[True] = True
    disclaimer: str


class CareerOrchestrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis_id: str = Field(min_length=16, max_length=128)
    job_title: str = Field(min_length=1, max_length=160)
    job_description: str = Field(min_length=1, max_length=10_000)
    geography: str | None = Field(default=None, min_length=1, max_length=120)
    essential_skills: list[str] = Field(default_factory=list, max_length=60)
    preferred_skills: list[str] = Field(default_factory=list, max_length=60)

    @field_validator("analysis_id")
    @classmethod
    def validate_orchestrator_analysis_id(cls, value: str) -> str:
        value = value.strip()
        if not 16 <= len(value) <= 128:
            raise ValueError("analysis identifier must contain 16 to 128 non-whitespace characters")
        return value

    @field_validator("job_title", "job_description", "geography")
    @classmethod
    def validate_orchestrator_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("essential_skills", "preferred_skills")
    @classmethod
    def validate_orchestrator_skills(cls, value: list[str]) -> list[str]:
        cleaned = [skill.strip() for skill in value]
        if any(not skill for skill in cleaned):
            raise ValueError("skills must not be blank")
        if any(len(skill) > 100 for skill in cleaned):
            raise ValueError("each skill must be 100 characters or fewer")
        return cleaned

    @model_validator(mode="after")
    def validate_orchestrator_requirement_count(self) -> "CareerOrchestrationRequest":
        total = len(self.essential_skills) + len(self.preferred_skills)
        if not total:
            raise ValueError("provide at least one essential or preferred skill")
        if total > 60:
            raise ValueError("essential and preferred skills combined must contain 60 or fewer items")
        return self


class CareerHumanReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["proceed", "revise", "stop"]
    reviewed_requirement_ids: list[str] = Field(min_length=1, max_length=60)
    review_notes: str | None = Field(default=None, max_length=1000)

    @field_validator("reviewed_requirement_ids")
    @classmethod
    def validate_reviewed_requirement_ids(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned) or len(set(cleaned)) != len(cleaned):
            raise ValueError("reviewed requirement identifiers must be nonblank and unique")
        return cleaned

    @field_validator("review_notes")
    @classmethod
    def validate_review_notes(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class SkillPassportSkill(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_name: str
    evidence_type: Literal["explicit", "inferred"]
    validation_status: Literal["evidence_found", "inference_unverified"]
    detection_method: Literal["ai", "local"] = "ai"

    @model_validator(mode="after")
    def validate_passport_evidence_status(self) -> "SkillPassportSkill":
        _ensure_evidence_status(self.evidence_type, self.validation_status)
        if self.detection_method == "local" and self.evidence_type != "explicit":
            raise ValueError("local deterministic detection cannot produce inferred passport skills")
        return self


class SkillPassportSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis_id: str
    analysis_mode: Literal["development", "local", "ai"]
    skill_count: int = Field(ge=0)
    explicit_count: int = Field(ge=0)
    inferred_count: int = Field(ge=0)
    skills: list[SkillPassportSkill]


CareerStageStatus = Literal[
    "complete", "unavailable", "needs_human_review", "blocked_pending_review", "stopped", "error",
]
CareerStageName = Literal[
    "skills_discovery", "market_intelligence", "job_audit", "inclusive_matching", "human_review", "learning_pathways",
]


class CareerWorkflowStage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: CareerStageName
    status: CareerStageStatus
    summary: str


class CareerHumanReviewState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["pending", "recorded"]
    decision: Literal["proceed", "revise", "stop"] | None = None
    reviewable_requirement_ids: list[str]
    reviewed_requirement_ids: list[str] = Field(default_factory=list)
    review_notes: str | None = None
    attested_at: datetime | None = None
    reviewer_identity_verified: Literal[False] = False


class CareerLearningStage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["blocked_pending_review", "complete", "stopped", "error"]
    message: str
    result: LearningPathwaysResponse | None = None


class CareerWorkflowRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    started_at: datetime
    workflow_status: Literal["awaiting_human_review", "revise_required", "stopped", "complete"]
    stages: list[CareerWorkflowStage]
    skill_passport: SkillPassportSummary
    market_intelligence: MarketIntelligenceResponse
    job_audit: BiasAuditResponse
    inclusive_matching: InclusiveMatchResponse
    human_review: CareerHumanReviewState
    learning_pathways: CareerLearningStage
    workflow_summary: str
    disclaimers: list[str]


class CareerWorkflowListItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    job_title: str
    job_description: str
    geography: str | None = None
    essential_skills: list[str]
    preferred_skills: list[str]
    workflow: CareerWorkflowRunResponse


ImpactCategory = Literal["all", "operations", "recruiter_feedback", "candidate_feedback", "baseline"]
ImpactAvailability = Literal["awaiting_data", "insufficient_sample", "measured", "unavailable"]


class DatabaseHealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["unconfigured", "reachable", "unavailable"]
    configured: bool
    reachable: bool


class RecruiterImpactFeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    submission_id: UUID
    workflow_id: str | None = Field(default=None, min_length=16, max_length=128)
    consent_to_aggregate: Literal[True]
    evidence_usefulness: int = Field(ge=1, le=5)
    explanation_clarity: int = Field(ge=1, le=5)
    review_ease: int = Field(ge=1, le=5)


class CandidateImpactFeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    submission_id: UUID
    consent_to_aggregate: Literal[True]
    skill_representation_clarity: int = Field(ge=1, le=5)
    understanding_of_skills: int = Field(ge=1, le=5)
    perceived_transparency: int = Field(ge=1, le=5)
    overall_experience: int = Field(ge=1, le=5)


class ImpactBaselineObservationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    submission_id: UUID
    consent_to_aggregate: Literal[True]
    review_duration_seconds: int = Field(ge=1, le=14_400)


class ImpactSubmissionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accepted: Literal[True] = True
    duplicate: bool = False
    message: str


class ImpactMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric_id: str
    name: str
    category: Literal["operations", "recruiter_feedback", "candidate_feedback", "baseline"]
    value: float | int | None
    unit: Literal["seconds", "count", "percent", "rating_1_to_5"]
    sample_size: int = Field(ge=0)
    period_start: datetime | None
    period_end: datetime | None
    data_availability: ImpactAvailability
    methodology: str
    limitations: list[str]


class ImpactDistributionItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    value: int | None
    sample_size: int = Field(ge=0)
    data_availability: ImpactAvailability


class ImpactTrendPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bucket_start: datetime
    average_seconds: float | None
    sample_size: int = Field(ge=0)
    data_availability: ImpactAvailability


class ImpactBaselineComparison(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data_availability: ImpactAvailability
    baseline_average_seconds: float | None
    pilot_average_seconds: float | None
    absolute_difference_seconds: float | None
    percentage_difference: float | None
    baseline_sample_size: int = Field(ge=0)
    pilot_sample_size: int = Field(ge=0)
    baseline_period_start: datetime | None
    baseline_period_end: datetime | None
    pilot_period_start: datetime | None
    pilot_period_end: datetime | None
    limitations: list[str]


class ImpactSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generated_at: datetime
    requested_start_date: str | None
    requested_end_date: str | None
    category: ImpactCategory
    pilot_period_label: str
    retention_seconds: int = Field(ge=1, le=7_776_000)
    dropped_event_count: int = Field(ge=0)
    minimum_feedback_sample: int = Field(ge=1)
    metrics: list[ImpactMetric]
    decision_distribution: list[ImpactDistributionItem]
    review_duration_trend: list[ImpactTrendPoint]
    recruiter_rating_distribution: list[ImpactDistributionItem]
    candidate_rating_distribution: list[ImpactDistributionItem]
    baseline_comparison: ImpactBaselineComparison
    disclaimer: str
