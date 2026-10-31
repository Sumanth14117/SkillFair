"""Centralized API error responses that omit submitted values and internals."""

import logging
import re
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from starlette.responses import JSONResponse

from .models import (
    AuthLoginRequest,
    AuthRegisterRequest,
    BiasAuditRequest,
    CandidateImpactFeedbackRequest,
    CareerHumanReviewRequest,
    CareerOrchestrationRequest,
    InclusiveMatchRequest,
    ImpactBaselineObservationRequest,
    JobMatchRequest,
    LearningPathwaysRequest,
    MarketIntelligenceRequest,
    RecruiterImpactFeedbackRequest,
    TransferableSkillsRequest,
)


_REQUEST_MODELS = (
    AuthLoginRequest,
    AuthRegisterRequest,
    BiasAuditRequest,
    CandidateImpactFeedbackRequest,
    CareerHumanReviewRequest,
    CareerOrchestrationRequest,
    InclusiveMatchRequest,
    ImpactBaselineObservationRequest,
    JobMatchRequest,
    LearningPathwaysRequest,
    MarketIntelligenceRequest,
    RecruiterImpactFeedbackRequest,
    TransferableSkillsRequest,
)
_SAFE_FIELDS = {name for model in _REQUEST_MODELS for name in model.model_fields}
_REQUEST_LOCATIONS = {"body", "query", "path", "header", "cookie", "form", "file"}
_SAFE_FIELDS.update({"start_date", "end_date", "category"})
_SAFE_ERROR_TYPES = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_ERROR_MESSAGES = {
    "missing": "A required value is missing.",
    "extra_forbidden": "This field is not supported.",
    "string_too_short": "This value is too short.",
    "string_too_long": "This value is too long.",
    "too_short": "This list has too few items.",
    "too_long": "This list has too many items.",
    "string_type": "Enter a text value.",
    "list_type": "Enter a list of values.",
    "int_type": "Enter a whole number.",
    "int_parsing": "Enter a valid whole number.",
    "literal_error": "Choose one of the supported values.",
    "value_error": "This value is invalid.",
    "model_type": "Enter an object with the required fields.",
    "dict_type": "Enter an object value.",
}


class _SuppressUnexpectedExceptionDetails(logging.Filter):
    """Avoid Uvicorn logging exception messages or tracebacks that may embed input."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.exc_info or record.exc_text or record.stack_info:
            record.msg = "Unhandled application exception; details suppressed."
            record.args = ()
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        return True


_server_error_logger = logging.getLogger("uvicorn.error")
if not any(isinstance(item, _SuppressUnexpectedExceptionDetails) for item in _server_error_logger.filters):
    _server_error_logger.addFilter(_SuppressUnexpectedExceptionDetails())


def _safe_location(location: Any) -> list[str | int]:
    if not isinstance(location, (tuple, list)):
        return ["request"]
    result: list[str | int] = []
    for part in location:
        if isinstance(part, str) and part in _REQUEST_LOCATIONS:
            result.append(part)
        elif isinstance(part, int) and not isinstance(part, bool):
            result.append(part)
        elif isinstance(part, str) and part in _SAFE_FIELDS:
            result.append(part)
        else:
            # In particular, do not reflect arbitrary mapping keys supplied by a client.
            result.append("field")
    return result or ["request"]


def _safe_validation_error(error: dict[str, Any]) -> dict[str, Any]:
    error_type = error.get("type")
    category = error_type if isinstance(error_type, str) and _SAFE_ERROR_TYPES.fullmatch(error_type) else "invalid_value"
    return {
        "location": _safe_location(error.get("loc")),
        "type": category,
        "message": _ERROR_MESSAGES.get(category, "Check this value and try again."),
    }


def request_validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    """Return field-oriented 422 details without echoing Pydantic input/context."""
    return JSONResponse(
        status_code=422,
        content={
            "detail": "Request validation failed.",
            "errors": [_safe_validation_error(error) for error in exc.errors()],
        },
    )


def unexpected_error_handler(_request: Request, _exc: Exception) -> JSONResponse:
    """Keep unexpected exception details out of API responses and logs."""
    return JSONResponse(status_code=500, content={"detail": "An unexpected server error occurred. Please try again."})
