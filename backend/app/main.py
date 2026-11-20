import os
import logging
import re
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone

from fastapi import Depends, FastAPI, File, Form, HTTPException, Path, Query, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from .analysis import analyze_skills, configured_provider_name, public_provider_status
from .auth import AuthContext, _allowed_origins, _require_csrf, get_auth_context, router as auth_router
from .error_handlers import request_validation_error_handler, unexpected_error_handler
from .bias_audit import audit_job
from .bias_audit_persistence import (
    delete_bias_audit,
    get_bias_audit,
    list_bias_audits,
    save_bias_audit,
    update_bias_audit_review,
)
from .career_orchestrator import (
    CareerWorkflowStore,
    CareerWorkflowReviewError,
    career_workflow_store,
    create_workflow_run,
    review_workflow_run,
)
from .learning_pathways import build_learning_pathways
from .navigation import list_notifications, mark_notification_read, search_account_records
from .inclusive_matching import build_inclusive_match
from .impact_measurement import impact_measurement_store
from .impact_persistence import (
    ImpactReferenceNotFound,
    get_account_impact_summary,
    submit_baseline_observation,
    submit_candidate_feedback,
    submit_recruiter_feedback,
)
from .database import check_database_health, dispose_database_engine, get_db_session
from .db_models import CareerWorkflow
from .dashboard import get_dashboard_overview
from .data_management import build_account_export, delete_analysis, delete_workflow, get_account_data
from .market_intelligence import MarketDataProviderError, market_intelligence_service
from .matching import match_job_skills
from .protections import RateLimitMiddleware, RequestBodyLimitMiddleware, SecurityHeadersMiddleware
from .transferable_skills import build_transferable_skills
from .models import (
    BiasAuditRequest,
    BiasAuditResponse,
    BiasAuditReviewUpdateRequest,
    SavedBiasAuditDetail,
    SavedBiasAuditListResponse,
    CareerWorkflowDetailResponse,
    CareerHumanReviewRequest,
    CareerWorkflowRunResponse,
    CareerOrchestrationRequest,
    JobMatchRequest,
    JobMatchResponse,
    InclusiveMatchRequest,
    InclusiveMatchResponse,
    CandidateImpactFeedbackRequest,
    ImpactBaselineObservationRequest,
    ImpactCategory,
    ImpactSubmissionResponse,
    ImpactSummaryResponse,
    DatabaseHealthResponse,
    DashboardOverviewResponse,
    LearningPathwaysRequest,
    LearningPathwaysResponse,
    MarketIntelligenceRequest,
    MarketIntelligenceResponse,
    MarketProviderStatusResponse,
    SkillsDiscoverResponse,
    SkillsProviderStatusResponse,
    RecruiterImpactFeedbackRequest,
    TransferableSkillsRequest,
    TransferableSkillsResponse,
    PersistedAnalysisResponse,
    CareerWorkflowSummary,
    AccountDataResponse,
    AccountExportResponse,
    SearchResponse,
    UserNotificationListResponse,
    UserNotificationResponse,
)
from .provenance import analysis_store
from .persistence import (
    apply_workflow_review,
    get_workflow as get_persisted_workflow,
    latest_analysis,
    list_workflows as list_persisted_workflows,
    persist_analysis,
    persist_workflow,
    record_workflow_review_start,
    resolve_analysis,
)
from .resume import validate_and_extract


_CAREER_RUN_PATH = re.compile(r"(/api/(?:career/orchestrator/runs|impact/reviews)/)[^/?\s]+(/review|/start)?([?#][^\s]*)?")


class _RedactCareerRunIdAccessLog(logging.Filter):
    """Avoid writing opaque workflow locators into default Uvicorn access logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name != "uvicorn.access":
            return True
        if isinstance(record.args, tuple) and len(record.args) > 2 and isinstance(record.args[2], str):
            arguments = list(record.args)
            arguments[2] = _CAREER_RUN_PATH.sub(r"\1[redacted]\2", arguments[2])
            record.args = tuple(arguments)
        request_line = getattr(record, "request_line", None)
        if isinstance(request_line, str):
            record.request_line = _CAREER_RUN_PATH.sub(r"\1[redacted]\2", request_line)
        return True


_uvicorn_access_logger = logging.getLogger("uvicorn.access")
if not any(isinstance(item, _RedactCareerRunIdAccessLog) for item in _uvicorn_access_logger.filters):
    _uvicorn_access_logger.addFilter(_RedactCareerRunIdAccessLog())

@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Do not require or connect to PostgreSQL at startup; release a lazily
    # created pool only when the application is shutting down.
    try:
        yield
    finally:
        dispose_database_engine()


app = FastAPI(title="SkillFair Skills Discovery API", version="0.2.0", lifespan=lifespan)
app.add_exception_handler(RequestValidationError, request_validation_error_handler)
app.add_exception_handler(Exception, unexpected_error_handler)
app.add_middleware(RequestBodyLimitMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(_allowed_origins()),
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["*"],
)
app.add_middleware(SecurityHeadersMiddleware)
app.include_router(auth_router)


_PUBLIC_RECORD_ID = r"^[A-Za-z0-9_-]{16,128}$"


@app.get("/api/account/data", response_model=AccountDataResponse)
def account_data_api(
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=100_000),
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db_session),
) -> AccountDataResponse:
    try:
        return get_account_data(db, context.user.id, limit=limit, offset=offset)
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=503, detail="Account data is temporarily unavailable.") from None


@app.get("/api/account/export", response_model=AccountExportResponse)
def account_export_api(
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db_session),
) -> Response:
    try:
        export = build_account_export(db, context.user.id)
        return Response(
            content=export.model_dump_json(),
            media_type="application/json",
            headers={"Content-Disposition": 'attachment; filename="skillfair-account-export.json"'},
        )
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=503, detail="Account export is temporarily unavailable.") from None


@app.delete("/api/account/analyses/{analysis_id}", status_code=204)
def delete_account_analysis_api(
    request: Request,
    analysis_id: str = Path(pattern=_PUBLIC_RECORD_ID),
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db_session),
) -> Response:
    _require_csrf(request, context)
    try:
        delete_analysis(db, context.user.id, analysis_id)
        return Response(status_code=204)
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=503, detail="The analysis could not be deleted. Please retry.") from None


@app.delete("/api/account/workflows/{workflow_id}", status_code=204)
def delete_account_workflow_api(
    request: Request,
    workflow_id: str = Path(pattern=_PUBLIC_RECORD_ID),
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db_session),
) -> Response:
    _require_csrf(request, context)
    try:
        delete_workflow(db, context.user.id, workflow_id)
        return Response(status_code=204)
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=503, detail="The workflow could not be deleted. Please retry.") from None


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/health/database", response_model=DatabaseHealthResponse)
def database_health() -> DatabaseHealthResponse:
    return check_database_health()


@app.get("/api/dashboard/overview", response_model=DashboardOverviewResponse)
def dashboard_overview_api(
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db_session),
) -> DashboardOverviewResponse:
    try:
        return get_dashboard_overview(db, context.user.id)
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=503, detail="Dashboard data is temporarily unavailable.") from None


@app.get("/api/skills/provider", response_model=SkillsProviderStatusResponse)
def skills_provider_status() -> SkillsProviderStatusResponse:
    return SkillsProviderStatusResponse.model_validate(public_provider_status())


def discover_skills(
    file: UploadFile = File(...),
    analysis_mode: str | None = Form(None),
    consent_to_gemini: str | None = Form(None),
    consent_to_ai_provider: str | None = Form(None),
    consented_ai_provider: str | None = Form(None),
    persist_in_memory: bool = True,
) -> SkillsDiscoverResponse:
    try:
        # Direct-call tests invoke this route function without FastAPI resolving
        # Form defaults; normalize those marker objects to their intended absence.
        if not isinstance(consent_to_gemini, str):
            consent_to_gemini = None
        if not isinstance(consent_to_ai_provider, str):
            consent_to_ai_provider = None
        if not isinstance(consented_ai_provider, str):
            consented_ai_provider = None
        if analysis_mode not in (None, "local", "ai"):
            raise HTTPException(status_code=422, detail="Choose local or AI analysis.")
        if consent_to_gemini not in (None, "true", "false"):
            raise HTTPException(status_code=422, detail="The legacy Gemini consent value is invalid.")
        if consent_to_ai_provider not in (None, "true", "false"):
            raise HTTPException(status_code=422, detail="The AI provider consent value is invalid.")
        if consented_ai_provider not in (None, "gemini", "groq"):
            raise HTTPException(status_code=422, detail="The consented AI provider value is invalid.")
        legacy_consent = consent_to_gemini == "true"
        provider_consent = consent_to_ai_provider == "true"
        if consent_to_gemini is not None and consent_to_ai_provider is not None and legacy_consent != provider_consent:
            raise HTTPException(status_code=422, detail="Conflicting AI provider consent values were supplied.")
        if analysis_mode == "local" and (legacy_consent or provider_consent):
            raise HTTPException(status_code=400, detail="External-provider consent is not applicable to local analysis.")
        if analysis_mode is None and (legacy_consent or provider_consent):
            raise HTTPException(status_code=400, detail="Select AI analysis before providing external-provider consent.")
        selected_provider = configured_provider_name() if analysis_mode == "ai" else "none"
        if provider_consent and consented_ai_provider != selected_provider:
            raise HTTPException(status_code=409, detail="The configured AI provider changed. Review its disclosure and consent again.")
        has_provider_consent = provider_consent or (selected_provider in {"gemini", "none"} and legacy_consent)
        if analysis_mode == "ai" and not has_provider_consent:
            provider_label = {"gemini": "Google Gemini", "groq": "Groq"}.get(selected_provider, "the configured AI provider")
            raise HTTPException(status_code=400, detail=f"Explicit consent is required before sending resume text to {provider_label}.")
        try:
            max_bytes = int(os.getenv("SKILLFAIR_MAX_UPLOAD_BYTES", str(10 * 1024 * 1024)))
            if not 1 <= max_bytes <= 50 * 1024 * 1024:
                raise ValueError("outside allowed bounds")
        except ValueError as exc:
            raise HTTPException(status_code=500, detail="The upload limit is misconfigured.") from exc
        # Read one byte over the limit to bound the in-process copy. A deployment
        # proxy should also cap request bodies before multipart parsing.
        content = file.file.read(max_bytes + 1)
        if len(content) > max_bytes:
            raise HTTPException(status_code=413, detail=f"The file exceeds the {max_bytes // (1024 * 1024)} MB upload limit.")
        text = validate_and_extract(file.filename, content, max_bytes)
        if selected_provider == "gemini":
            # Preserve the existing Gemini call shape for clients and tests while
            # allowing the generic consent field to authorize this configured provider.
            result = analyze_skills(text, analysis_mode, has_provider_consent)
        elif selected_provider == "groq":
            result = analyze_skills(text, analysis_mode, False, consent_to_ai_provider=provider_consent)
        elif analysis_mode == "local":
            result = analyze_skills(text, analysis_mode, False)
        elif legacy_consent and not provider_consent:
            result = analyze_skills(text, analysis_mode, legacy_consent)
        else:
            result = analyze_skills(text, analysis_mode, False, consent_to_ai_provider=provider_consent)
        if persist_in_memory:
            analysis_id = analysis_store.create(result.skills, result.analysis_mode)
            return result.model_copy(update={"analysis_id": analysis_id})
        return result
    finally:
        # Explicitly close Starlette's spooled upload file on success and error.
        file.file.close()


def match_job(request: JobMatchRequest) -> JobMatchResponse:
    candidate_skills = analysis_store.resolve(request.analysis_id)
    if candidate_skills is None:
        raise HTTPException(status_code=404, detail="Skills analysis was not found or has expired. Run Skills Discovery again.")
    return match_job_skills(request, candidate_skills)


def inclusive_match(request: InclusiveMatchRequest) -> InclusiveMatchResponse:
    candidate_skills = analysis_store.resolve(request.analysis_id)
    if candidate_skills is None:
        raise HTTPException(status_code=404, detail="Skills analysis was not found or has expired. Run Skills Discovery again.")
    return build_inclusive_match(request, candidate_skills)


@app.post("/api/jobs/audit", response_model=BiasAuditResponse)
def audit_job_description(request: BiasAuditRequest) -> BiasAuditResponse:
    return audit_job(request)


@app.post("/api/jobs/audits", response_model=SavedBiasAuditDetail)
def save_job_audit_api(
    request: BiasAuditRequest,
    http_request: Request,
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db_session),
) -> SavedBiasAuditDetail:
    _require_csrf(http_request, context)
    try:
        return save_bias_audit(db, context.user.id, request)
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=503, detail="The audit could not be saved. Please retry.") from None


@app.get("/api/jobs/audits", response_model=SavedBiasAuditListResponse)
def list_job_audits_api(
    limit: int = Query(default=20, ge=1, le=30),
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db_session),
) -> SavedBiasAuditListResponse:
    return list_bias_audits(db, context.user.id, limit=limit)


@app.get("/api/jobs/audits/{audit_id}", response_model=SavedBiasAuditDetail)
def get_job_audit_api(
    audit_id: str = Path(pattern=r"^[a-f0-9]{32}$"),
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db_session),
) -> SavedBiasAuditDetail:
    result = get_bias_audit(db, context.user.id, audit_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Saved audit was not found.")
    return result


@app.patch("/api/jobs/audits/{audit_id}/findings/{finding_index}", response_model=SavedBiasAuditDetail)
def update_job_audit_review_api(
    request: BiasAuditReviewUpdateRequest,
    http_request: Request,
    audit_id: str = Path(pattern=r"^[a-f0-9]{32}$"),
    finding_index: int = Path(ge=0, le=100),
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db_session),
) -> SavedBiasAuditDetail:
    _require_csrf(http_request, context)
    try:
        result = update_bias_audit_review(db, context.user.id, audit_id, finding_index, request)
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=503, detail="The audit review could not be saved. Please retry.") from None
    if result is None:
        raise HTTPException(status_code=404, detail="Saved audit finding was not found.")
    return result


@app.delete("/api/jobs/audits/{audit_id}", status_code=204)
def delete_job_audit_api(
    http_request: Request,
    audit_id: str = Path(pattern=r"^[a-f0-9]{32}$"),
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db_session),
) -> Response:
    _require_csrf(http_request, context)
    try:
        if not delete_bias_audit(db, context.user.id, audit_id):
            raise HTTPException(status_code=404, detail="Saved audit was not found.")
        return Response(status_code=204)
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=503, detail="The audit could not be deleted. Please retry.") from None


def learning_pathways(request: LearningPathwaysRequest) -> LearningPathwaysResponse:
    candidate_skills = None
    if request.analysis_id is not None:
        candidate_skills = analysis_store.resolve(request.analysis_id)
        if candidate_skills is None:
            raise HTTPException(
                status_code=404,
                detail="Skills analysis was not found or has expired. Run Skills Discovery again.",
            )
    return build_learning_pathways(request, candidate_skills)


@app.post("/api/market/intelligence", response_model=MarketIntelligenceResponse)
def market_intelligence(request: MarketIntelligenceRequest) -> MarketIntelligenceResponse:
    try:
        return market_intelligence_service.query(request)
    except MarketDataProviderError as exc:
        raise HTTPException(
            status_code=502,
            detail="The configured market-data source is unavailable or returned invalid provenance.",
        ) from exc


@app.get("/api/market/provider-status", response_model=MarketProviderStatusResponse)
def market_provider_status() -> MarketProviderStatusResponse:
    return market_intelligence_service.provider_status()


def start_impact_review(run_id: str) -> ImpactSubmissionResponse:
    record = career_workflow_store.resolve(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Career workflow was not found or has expired.")
    if record.response.human_review.status != "pending":
        raise HTTPException(status_code=409, detail="This workflow is not awaiting human review.")
    recorded = impact_measurement_store.record_review_start(run_id, datetime.now(timezone.utc))
    if recorded is None:
        raise HTTPException(status_code=503, detail="Temporary impact measurement capacity is full. Review can continue, but this session may not be measured.")
    return ImpactSubmissionResponse(duplicate=not recorded, message="Review start recorded for temporary aggregate measurement.")


@app.post("/api/impact/feedback/recruiter", response_model=ImpactSubmissionResponse)
def submit_recruiter_impact_feedback(
    request: RecruiterImpactFeedbackRequest,
    http_request: Request,
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db_session),
) -> ImpactSubmissionResponse:
    _require_csrf(http_request, context)
    try:
        return submit_recruiter_feedback(db, context.user.id, request)
    except ImpactReferenceNotFound as exc:
        raise HTTPException(status_code=404, detail="The referenced workflow was not found.") from exc
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail="Recruiter feedback could not be stored. Please retry.") from exc


@app.post("/api/impact/feedback/candidate", response_model=ImpactSubmissionResponse)
def submit_candidate_impact_feedback(
    request: CandidateImpactFeedbackRequest,
    http_request: Request,
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db_session),
) -> ImpactSubmissionResponse:
    _require_csrf(http_request, context)
    try:
        return submit_candidate_feedback(db, context.user.id, request)
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=503, detail="Candidate feedback could not be stored. Please retry.") from None


@app.post("/api/impact/baseline-observations", response_model=ImpactSubmissionResponse)
def submit_impact_baseline_observation(
    request: ImpactBaselineObservationRequest,
    http_request: Request,
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db_session),
) -> ImpactSubmissionResponse:
    _require_csrf(http_request, context)
    try:
        return submit_baseline_observation(db, context.user.id, request)
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail="The baseline observation could not be stored. Please retry.") from exc


@app.get("/api/impact/summary", response_model=ImpactSummaryResponse)
def get_impact_summary(
    start_date: date | None = None,
    end_date: date | None = None,
    category: ImpactCategory = "all",
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db_session),
) -> ImpactSummaryResponse:
    try:
        return get_account_impact_summary(db, context.user.id, category=category, start_date=start_date, end_date=end_date)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="The measurement date range is invalid.") from exc
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail="The impact summary is temporarily unavailable.") from exc


def transferable_skills(request: TransferableSkillsRequest) -> TransferableSkillsResponse:
    candidate_skills = None
    if request.analysis_id is not None:
        candidate_skills = analysis_store.resolve(request.analysis_id)
        if candidate_skills is None:
            raise HTTPException(
                status_code=404,
                detail="Skills analysis was not found or has expired. Run Skills Discovery again.",
            )
    return build_transferable_skills(request, candidate_skills)


def create_career_workflow(request: CareerOrchestrationRequest) -> CareerWorkflowRunResponse:
    candidate_analysis = analysis_store.resolve_with_mode(request.analysis_id)
    if candidate_analysis is None:
        raise HTTPException(status_code=404, detail="Skills analysis was not found or has expired. Run Skills Discovery again.")
    candidate_skills, analysis_mode = candidate_analysis
    return create_workflow_run(request, candidate_skills, career_workflow_store, analysis_mode=analysis_mode)


def get_career_workflow(run_id: str) -> CareerWorkflowRunResponse:
    record = career_workflow_store.resolve(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Career workflow was not found or has expired. Start a new workflow.")
    # resolve() returns a deep-copied typed response and does not create or advance a run.
    return CareerWorkflowRunResponse.model_validate(record.response)


def review_career_workflow(run_id: str, request: CareerHumanReviewRequest) -> CareerWorkflowRunResponse:
    try:
        result = review_workflow_run(run_id, request, career_workflow_store)
    except CareerWorkflowReviewError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Career workflow was not found or has expired. Start a new workflow.")
    return result


# Authenticated, database-backed API handlers. The functions above remain small
# domain-test adapters for the pre-persistence unit tests; only these handlers
# are registered as HTTP routes.
@app.post("/api/skills/discover", response_model=SkillsDiscoverResponse)
def discover_skills_api(
    request: Request,
    file: UploadFile = File(...),
    analysis_mode: str | None = Form(None),
    consent_to_gemini: str | None = Form(None),
    consent_to_ai_provider: str | None = Form(None),
    consented_ai_provider: str | None = Form(None),
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db_session),
) -> SkillsDiscoverResponse:
    _require_csrf(request, context)
    result = discover_skills(
        file,
        analysis_mode=analysis_mode,
        consent_to_gemini=consent_to_gemini,
        consent_to_ai_provider=consent_to_ai_provider,
        consented_ai_provider=consented_ai_provider,
        persist_in_memory=False,
    )
    persisted = persist_analysis(db, context.user.id, result.analysis_mode, result.skills)
    return result.model_copy(update={"analysis_id": persisted.analysis_id})


@app.get("/api/skills/analyses/latest", response_model=PersistedAnalysisResponse | None)
def get_latest_analysis_api(
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db_session),
) -> PersistedAnalysisResponse | None:
    return latest_analysis(db, context.user.id)


@app.get("/api/skills/analyses/{analysis_id}", response_model=PersistedAnalysisResponse)
def get_analysis_api(
    analysis_id: str = Path(pattern=_PUBLIC_RECORD_ID),
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db_session),
) -> PersistedAnalysisResponse:
    resolved = resolve_analysis(db, context.user.id, analysis_id)
    if resolved is None:
        raise HTTPException(status_code=404, detail="Skills analysis was not found.")
    row, skills = resolved
    return PersistedAnalysisResponse(
        analysis_id=row.public_id,
        analysis_mode=row.analysis_mode,
        created_at=row.created_at,
        skills=skills,
    )


@app.get("/api/search", response_model=SearchResponse)
def account_search_api(
    q: str = Query(min_length=2, max_length=80),
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db_session),
) -> SearchResponse:
    try:
        return search_account_records(db, context.user.id, q)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=503, detail="Search is temporarily unavailable.") from None


@app.get("/api/notifications", response_model=UserNotificationListResponse)
def account_notifications_api(
    limit: int = Query(default=20, ge=1, le=50),
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db_session),
) -> UserNotificationListResponse:
    try:
        return list_notifications(db, context.user.id, limit)
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=503, detail="Notifications are temporarily unavailable.") from None


@app.post("/api/notifications/{notification_id}/read", response_model=UserNotificationResponse)
def mark_account_notification_read_api(
    request: Request,
    notification_id: str = Path(pattern=r"^[A-Fa-f0-9]{32}$"),
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db_session),
) -> UserNotificationResponse:
    _require_csrf(request, context)
    try:
        row = mark_notification_read(db, context.user.id, notification_id)
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=503, detail="The notification could not be updated.") from None
    if row is None:
        raise HTTPException(status_code=404, detail="Notification was not found.")
    workflow_public_id = None
    if row.workflow_id is not None:
        workflow = db.scalar(select(CareerWorkflow.public_id).where(
            CareerWorkflow.id == row.workflow_id,
            CareerWorkflow.owner_user_id == context.user.id,
        ))
        workflow_public_id = workflow
    return UserNotificationResponse(
        notification_id=row.public_id,
        notification_type=row.notification_type,
        title=row.title,
        message=row.message,
        created_at=row.created_at,
        read_at=row.read_at,
        workflow_id=workflow_public_id,
    )


@app.post("/api/jobs/match", response_model=JobMatchResponse)
def match_job_api(
    request: JobMatchRequest,
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db_session),
) -> JobMatchResponse:
    resolved = resolve_analysis(db, context.user.id, request.analysis_id)
    if resolved is None:
        raise HTTPException(status_code=404, detail="Skills analysis was not found. Run Skills Discovery again.")
    _, skills = resolved
    return match_job_skills(request, skills)


@app.post("/api/jobs/inclusive-match", response_model=InclusiveMatchResponse)
def inclusive_match_api(
    request: InclusiveMatchRequest,
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db_session),
) -> InclusiveMatchResponse:
    resolved = resolve_analysis(db, context.user.id, request.analysis_id)
    if resolved is None:
        raise HTTPException(status_code=404, detail="Skills analysis was not found. Run Skills Discovery again.")
    _, skills = resolved
    return build_inclusive_match(request, skills)


@app.post("/api/learning/pathways", response_model=LearningPathwaysResponse)
def learning_pathways_api(
    request: LearningPathwaysRequest,
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db_session),
) -> LearningPathwaysResponse:
    skills = None
    if request.analysis_id is not None:
        resolved = resolve_analysis(db, context.user.id, request.analysis_id)
        if resolved is None:
            raise HTTPException(status_code=404, detail="Skills analysis was not found. Run Skills Discovery again.")
        _, skills = resolved
    return build_learning_pathways(request, skills)


@app.post("/api/skills/transferability", response_model=TransferableSkillsResponse)
def transferable_skills_api(
    request: TransferableSkillsRequest,
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db_session),
) -> TransferableSkillsResponse:
    skills = None
    if request.analysis_id is not None:
        resolved = resolve_analysis(db, context.user.id, request.analysis_id)
        if resolved is None:
            raise HTTPException(status_code=404, detail="Skills analysis was not found. Run Skills Discovery again.")
        _, skills = resolved
    return build_transferable_skills(request, skills)


@app.get("/api/career/orchestrator/runs", response_model=list[CareerWorkflowSummary])
def list_career_workflows_api(
    limit: int = Query(default=20, ge=1, le=30),
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db_session),
) -> list[CareerWorkflowSummary]:
    return list_persisted_workflows(db, context.user.id, limit=limit)


@app.post("/api/career/orchestrator/runs", response_model=CareerWorkflowRunResponse)
def create_career_workflow_api(
    request: CareerOrchestrationRequest,
    http_request: Request,
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db_session),
) -> CareerWorkflowRunResponse:
    _require_csrf(http_request, context)
    resolved = resolve_analysis(db, context.user.id, request.analysis_id)
    if resolved is None:
        raise HTTPException(status_code=404, detail="Skills analysis was not found. Run Skills Discovery again.")
    analysis, skills = resolved
    response = create_workflow_run(request, skills, CareerWorkflowStore(), analysis_mode=analysis.analysis_mode)
    return persist_workflow(db, context.user.id, analysis, request, response)


@app.get("/api/career/orchestrator/runs/{run_id}", response_model=CareerWorkflowDetailResponse)
def get_career_workflow_api(
    run_id: str,
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db_session),
) -> CareerWorkflowDetailResponse:
    response = get_persisted_workflow(db, context.user.id, run_id)
    if response is None:
        raise HTTPException(status_code=404, detail="Career workflow was not found.")
    return response


@app.post("/api/career/orchestrator/runs/{run_id}/review", response_model=CareerWorkflowRunResponse)
def review_career_workflow_api(
    run_id: str,
    request: CareerHumanReviewRequest,
    http_request: Request,
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db_session),
) -> CareerWorkflowRunResponse:
    _require_csrf(http_request, context)
    try:
        response = apply_workflow_review(db, context.user.id, run_id, request, context.user.id)
    except CareerWorkflowReviewError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if response is None:
        raise HTTPException(status_code=404, detail="Career workflow was not found.")
    # The committed workflow and review rows are the sole source of review
    # measurements. No duplicate event is written after the transaction.
    return response


@app.post("/api/impact/reviews/{run_id}/start", response_model=ImpactSubmissionResponse)
def start_impact_review_api(
    run_id: str,
    request: Request,
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db_session),
) -> ImpactSubmissionResponse:
    _require_csrf(request, context)
    response = get_persisted_workflow(db, context.user.id, run_id)
    if response is None:
        raise HTTPException(status_code=404, detail="Career workflow was not found.")
    if response.human_review.status != "pending":
        raise HTTPException(status_code=409, detail="This workflow is not awaiting human review.")
    recorded = record_workflow_review_start(db, context.user.id, run_id)
    if recorded is None:
        raise HTTPException(status_code=404, detail="Career workflow was not found.")
    return ImpactSubmissionResponse(duplicate=not recorded, message="Review start recorded with the persistent workflow.")
