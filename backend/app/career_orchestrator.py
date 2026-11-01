from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from secrets import token_urlsafe
from threading import RLock
from time import monotonic
from typing import Callable

from .bias_audit import audit_job
from .inclusive_matching import build_inclusive_match
from .learning_pathways import build_learning_pathways
from .market_intelligence import MarketDataProviderError, market_intelligence_service
from .models import (
    AuditCriterion,
    BiasAuditRequest,
    CareerHumanReviewRequest,
    CareerHumanReviewState,
    CareerLearningStage,
    CareerOrchestrationRequest,
    CareerWorkflowRunResponse,
    CareerWorkflowStage,
    InclusiveMatchRequest,
    LearningPathwaysRequest,
    MarketIntelligenceRequest,
    MarketIntelligenceResponse,
    SkillEvidence,
    SkillPassportSkill,
    SkillPassportSummary,
)


_WORKFLOW_TTL_SECONDS = 30 * 60
_WORKFLOW_MAX_ENTRIES = 200
_WORKFLOW_DISCLAIMERS = [
    "The workflow supports recruiter review and does not rank, reject, or select candidates.",
    "Missing evidence does not prove missing ability; inferred skills remain unverified.",
    "Potential transfers are adjacent capability signals, not equivalent skills or proof of qualification.",
    "Bias Audit findings are advisory and do not prove fairness or legal compliance.",
    "The review action is a user attestation; reviewer identity is not authenticated.",
]


@dataclass(frozen=True)
class _WorkflowRecord:
    expires_at: float
    request: CareerOrchestrationRequest
    skills: tuple[SkillEvidence, ...]
    response: CareerWorkflowRunResponse


class CareerWorkflowStore:
    """Bounded process-local workflow state; stores validated skills, never resume text."""

    def __init__(
        self,
        ttl_seconds: int = _WORKFLOW_TTL_SECONDS,
        max_entries: int = _WORKFLOW_MAX_ENTRIES,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if ttl_seconds <= 0 or max_entries <= 0:
            raise ValueError("ttl_seconds and max_entries must be positive")
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._clock = clock
        self._records: OrderedDict[str, _WorkflowRecord] = OrderedDict()
        self._lock = RLock()

    def _remove_expired(self, now: float) -> None:
        expired = [key for key, record in self._records.items() if record.expires_at <= now]
        for key in expired:
            self._records.pop(key, None)

    @staticmethod
    def _copy(record: _WorkflowRecord) -> _WorkflowRecord:
        return _WorkflowRecord(
            expires_at=record.expires_at,
            request=record.request.model_copy(deep=True),
            skills=tuple(skill.model_copy(deep=True) for skill in record.skills),
            response=record.response.model_copy(deep=True),
        )

    def create(self, run_id: str, request: CareerOrchestrationRequest, skills: list[SkillEvidence], response: CareerWorkflowRunResponse) -> bool:
        now = self._clock()
        safe_skills = tuple(skill.model_copy(deep=True) for skill in skills)
        safe_request = request.model_copy(deep=True)
        safe_response = response.model_copy(deep=True)
        with self._lock:
            self._remove_expired(now)
            if run_id in self._records:
                return False
            self._records[run_id] = _WorkflowRecord(now + self._ttl_seconds, safe_request, safe_skills, safe_response)
            while len(self._records) > self._max_entries:
                self._records.popitem(last=False)
        return True

    def resolve(self, run_id: str) -> _WorkflowRecord | None:
        now = self._clock()
        with self._lock:
            self._remove_expired(now)
            record = self._records.get(run_id)
            if record is None:
                return None
            self._records.move_to_end(run_id)
            return self._copy(record)

    def replace(self, run_id: str, request: CareerOrchestrationRequest, skills: tuple[SkillEvidence, ...], response: CareerWorkflowRunResponse) -> bool:
        now = self._clock()
        with self._lock:
            self._remove_expired(now)
            if run_id not in self._records:
                return False
            self._records[run_id] = _WorkflowRecord(
                now + self._ttl_seconds,
                request.model_copy(deep=True),
                tuple(skill.model_copy(deep=True) for skill in skills),
                response.model_copy(deep=True),
            )
            self._records.move_to_end(run_id)
            return True

    def apply_review_transition(
        self,
        run_id: str,
        transition: Callable[[_WorkflowRecord], tuple[CareerWorkflowRunResponse, bool]],
    ) -> CareerWorkflowRunResponse | None:
        """Run review validation and commit under one lock acquisition."""
        with self._lock:
            now = self._clock()
            self._remove_expired(now)
            record = self._records.get(run_id)
            if record is None:
                return None
            self._records.move_to_end(run_id)

            response, changed = transition(self._copy(record))
            now = self._clock()
            self._remove_expired(now)
            if self._records.get(run_id) is not record:
                return None
            if not changed:
                return response.model_copy(deep=True)

            committed = _WorkflowRecord(
                now + self._ttl_seconds,
                record.request.model_copy(deep=True),
                tuple(skill.model_copy(deep=True) for skill in record.skills),
                response.model_copy(deep=True),
            )
            self._records[run_id] = committed
            self._records.move_to_end(run_id)
            return committed.response.model_copy(deep=True)

    def __len__(self) -> int:
        with self._lock:
            self._remove_expired(self._clock())
            return len(self._records)


career_workflow_store = CareerWorkflowStore()


class CareerWorkflowReviewError(Exception):
    """Safe workflow review validation/conflict error."""


def _initial_stages(
    skill_count: int,
    market_status: str,
    market_summary: str,
    finding_count: int,
    requirement_count: int,
) -> list[CareerWorkflowStage]:
    return [
        CareerWorkflowStage(stage="skills_discovery", status="complete", summary=f"Resolved {skill_count} validated skill records from the existing Skills Discovery analysis."),
        CareerWorkflowStage(stage="market_intelligence", status=market_status, summary=market_summary),
        CareerWorkflowStage(stage="job_audit", status="complete", summary=f"Completed deterministic criteria review; {finding_count} advisory finding(s) returned."),
        CareerWorkflowStage(stage="inclusive_matching", status="complete", summary=f"Reviewed {requirement_count} unique essential and preferred requirement(s) against validated evidence."),
        CareerWorkflowStage(stage="human_review", status="needs_human_review", summary="Recruiter review and explicit decision are required before learning activities are generated."),
        CareerWorkflowStage(stage="learning_pathways", status="blocked_pending_review", summary="Learning recommendations are held until the reviewer chooses to proceed."),
    ]


def create_workflow_run(
    request: CareerOrchestrationRequest,
    skills: list[SkillEvidence],
    store: CareerWorkflowStore = career_workflow_store,
    analysis_mode: str = "ai",
) -> CareerWorkflowRunResponse:
    try:
        market = market_intelligence_service.query(MarketIntelligenceRequest(job_title=request.job_title, geography=request.geography))
        market_stage_status = "complete" if market.data_availability == "available" else "unavailable"
    except MarketDataProviderError:
        market = MarketIntelligenceResponse(
            job_title=request.job_title,
            geography=request.geography,
            data_availability="unavailable",
            sources=[],
            insights=[],
            message="The configured market-data provider failed. No verified market insights are available for this workflow.",
        )
        market_stage_status = "error"

    audit_request = BiasAuditRequest(
        job_title=request.job_title,
        job_description=request.job_description,
        requirements=[
            *(AuditCriterion(text=skill, importance="essential") for skill in request.essential_skills),
            *(AuditCriterion(text=skill, importance="preferred") for skill in request.preferred_skills),
        ],
    )
    audit = audit_job(audit_request)
    inclusive_request = InclusiveMatchRequest(
        analysis_id=request.analysis_id,
        job_title=request.job_title,
        job_description=request.job_description,
        essential_skills=request.essential_skills,
        preferred_skills=request.preferred_skills,
    )
    inclusive = build_inclusive_match(inclusive_request, skills, precomputed_audit=audit)

    passport_skills = [SkillPassportSkill(
        skill_name=skill.skill_name,
        evidence_type=skill.evidence_type,
        validation_status=skill.validation_status,
        detection_method=skill.detection_method,
    ) for skill in skills]
    passport = SkillPassportSummary(
        analysis_id=request.analysis_id,
        analysis_mode=analysis_mode,
        skill_count=len(skills),
        explicit_count=sum(skill.evidence_type == "explicit" for skill in skills),
        inferred_count=sum(skill.evidence_type == "inferred" for skill in skills),
        skills=passport_skills,
    )
    reviewable_ids = [f"req-{index:03d}" for index, _ in enumerate(inclusive.requirements, start=1)]
    stages = _initial_stages(len(skills), market_stage_status, market.message, audit.finding_count, len(inclusive.requirements))
    started_at = datetime.now(timezone.utc)

    run_id = ""
    response = CareerWorkflowRunResponse(
        run_id="pending",
        started_at=started_at,
        workflow_status="awaiting_human_review",
        stages=stages,
        skill_passport=passport,
        market_intelligence=market,
        job_audit=audit,
        inclusive_matching=inclusive,
        human_review=CareerHumanReviewState(
            status="pending",
            reviewable_requirement_ids=reviewable_ids,
            reviewer_identity_verified=False,
        ),
        learning_pathways=CareerLearningStage(
            status="blocked_pending_review",
            message="Learning Pathways will be generated only after an explicit proceed decision.",
        ),
        workflow_summary="Role criteria and candidate evidence are ready for recruiter review. Learning Pathways are awaiting an explicit human decision.",
        disclaimers=_WORKFLOW_DISCLAIMERS.copy(),
    )
    while not run_id:
        candidate_id = token_urlsafe(32)
        if store.create(candidate_id, request, skills, response.model_copy(update={"run_id": candidate_id})):
            run_id = candidate_id
    return response.model_copy(update={"run_id": run_id})


def _build_review_transition(
    record: _WorkflowRecord,
    request: CareerHumanReviewRequest,
) -> tuple[CareerWorkflowRunResponse, bool]:
    current = record.response
    if current.human_review.status == "recorded":
        if current.human_review.decision == request.decision and set(current.human_review.reviewed_requirement_ids) == set(request.reviewed_requirement_ids) and current.human_review.review_notes == request.review_notes:
            return current, False
        raise CareerWorkflowReviewError("A review decision has already been recorded for this workflow.")
    if current.human_review.status != "pending" or current.workflow_status != "awaiting_human_review":
        raise CareerWorkflowReviewError("This workflow is no longer pending human review.")

    allowed = set(current.human_review.reviewable_requirement_ids)
    submitted = set(request.reviewed_requirement_ids)
    if submitted != allowed:
        raise CareerWorkflowReviewError("Review every listed requirement exactly once before recording a decision.")

    human_review = CareerHumanReviewState(
        status="recorded",
        decision=request.decision,
        reviewable_requirement_ids=current.human_review.reviewable_requirement_ids,
        reviewed_requirement_ids=request.reviewed_requirement_ids,
        review_notes=request.review_notes,
        attested_at=datetime.now(timezone.utc),
        reviewer_identity_verified=False,
    )
    stages = [stage.model_copy(deep=True) for stage in current.stages]
    human_index = next(index for index, stage in enumerate(stages) if stage.stage == "human_review")
    learning_index = next(index for index, stage in enumerate(stages) if stage.stage == "learning_pathways")

    if request.decision == "proceed":
        pathways = build_learning_pathways(
            LearningPathwaysRequest(
                analysis_id=record.request.analysis_id,
                job_title=record.request.job_title,
                required_skills=[item.skill for item in current.inclusive_matching.requirements],
            ),
            list(record.skills),
        )
        stages[human_index] = stages[human_index].model_copy(update={"status": "complete", "summary": "Reviewer attested that all listed requirements were reviewed and chose to proceed."})
        stages[learning_index] = stages[learning_index].model_copy(update={"status": "complete", "summary": "Deterministic learning activities were generated after the proceed attestation."})
        learning = CareerLearningStage(status="complete", message="Learning activities are advisory and are not completed training or verified qualifications.", result=pathways)
        workflow_status = "complete"
        summary = "Recruiter review was recorded and Learning Pathways were generated. The workflow does not make a hiring decision."
    elif request.decision == "revise":
        stages[human_index] = stages[human_index].model_copy(update={"status": "needs_human_review", "summary": "Reviewer requested criteria revision; update the role inputs and start a new workflow run."})
        stages[learning_index] = stages[learning_index].model_copy(update={"status": "blocked_pending_review", "summary": "Learning activities remain blocked until revised criteria receive a new review."})
        learning = current.learning_pathways.model_copy(update={"status": "blocked_pending_review", "message": "No learning activities were generated. Revise the criteria and create a new workflow run."})
        workflow_status = "revise_required"
        summary = "The reviewer requested criteria revision. No requirements were silently changed and no learning activities were generated."
    else:
        stages[human_index] = stages[human_index].model_copy(update={"status": "stopped", "summary": "Reviewer chose to stop this workflow."})
        stages[learning_index] = stages[learning_index].model_copy(update={"status": "stopped", "summary": "Learning activities were not generated because the workflow was stopped."})
        learning = current.learning_pathways.model_copy(update={"status": "stopped", "message": "The workflow was stopped by the reviewer; no learning activities were generated."})
        workflow_status = "stopped"
        summary = "The reviewer stopped the workflow. No learning activities or hiring decision were generated."

    response = current.model_copy(update={
        "workflow_status": workflow_status,
        "stages": stages,
        "human_review": human_review,
        "learning_pathways": learning,
        "workflow_summary": summary,
    })
    return response, True


def review_workflow_run(
    run_id: str,
    request: CareerHumanReviewRequest,
    store: CareerWorkflowStore = career_workflow_store,
) -> CareerWorkflowRunResponse | None:
    return store.apply_review_transition(
        run_id,
        lambda record: _build_review_transition(record, request),
    )
