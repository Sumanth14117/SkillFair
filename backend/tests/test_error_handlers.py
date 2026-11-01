import json
import logging
import sys

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from app.error_handlers import request_validation_error_handler, unexpected_error_handler
from app.main import health
from app.models import JobMatchRequest


def _request(path: str = "/api/jobs/match") -> Request:
    return Request({
        "type": "http", "http_version": "1.1", "method": "POST", "scheme": "http",
        "path": path, "raw_path": path.encode(), "query_string": b"", "headers": [],
        "server": ("test", 80), "client": ("test", 1234), "root_path": "",
    })


def _body(response) -> dict:
    return json.loads(response.body)


def test_validation_errors_include_safe_locations_categories_and_status_without_inputs():
    sensitive_email = "private.person@example.test"
    sensitive_secret = "submitted-secret-value"
    payload = {
        "analysis_id": "short",
        "job_title": sensitive_email + ("x" * 180),
        "job_description": {"nested": sensitive_secret},
        "required_skills": ["Python", {"secret": sensitive_secret}],
        "unexpected_private_field": sensitive_secret,
    }
    try:
        JobMatchRequest.model_validate(payload)
    except ValidationError as exc:
        validation_error = RequestValidationError(
            [{**error, "loc": ("body", *error["loc"])} for error in exc.errors()],
            body=payload,
        )
    else:
        raise AssertionError("fixture must fail request validation")

    response = request_validation_error_handler(_request(), validation_error)
    body = _body(response)
    assert response.status_code == 422
    assert body["detail"] == "Request validation failed."
    assert all(set(item) == {"location", "type", "message"} for item in body["errors"])
    assert any(item["location"] == ["body", "job_title"] for item in body["errors"])
    assert any(item["location"] == ["body", "required_skills", 1] for item in body["errors"])
    assert any("field" in item["location"] for item in body["errors"])
    serialized = json.dumps(body)
    assert sensitive_email not in serialized
    assert sensitive_secret not in serialized
    assert "input" not in serialized


def test_file_upload_validation_error_omits_submitted_file_value():
    submitted_value = "resume text with private.person@example.test"
    exc = RequestValidationError([{
        "type": "missing", "loc": ("body", "file"), "input": submitted_value,
    }], body={"file": submitted_value})

    response = request_validation_error_handler(_request("/api/skills/discover"), exc)
    assert response.status_code == 422
    assert _body(response) == {
        "detail": "Request validation failed.",
        "errors": [{"location": ["body", "file"], "type": "missing", "message": "A required value is missing."}],
    }
    assert submitted_value not in response.body.decode()


def test_unexpected_server_exception_returns_generic_500_without_internal_details():
    secret_exception = "secret-value /private/path/resume.pdf"
    response = unexpected_error_handler(_request(), RuntimeError(secret_exception))
    assert response.status_code == 500
    assert _body(response) == {"detail": "An unexpected server error occurred. Please try again."}
    assert secret_exception not in response.body.decode()
    assert "RuntimeError" not in response.body.decode()


def test_unexpected_exception_log_record_does_not_retain_exception_details():
    secret = "resume-contact private.person@example.test"
    try:
        raise RuntimeError(secret)
    except RuntimeError:
        record = logging.LogRecord(
            "uvicorn.error", logging.ERROR, __file__, 1, "Exception in ASGI application", (), sys.exc_info()
        )
    logger = logging.getLogger("uvicorn.error")
    sanitizer = next(item for item in logger.filters if item.__class__.__name__ == "_SuppressUnexpectedExceptionDetails")
    assert sanitizer.filter(record)
    assert record.getMessage() == "Unhandled application exception; details suppressed."
    assert record.exc_info is None
    assert secret not in record.getMessage()


def test_successful_health_response_contract_is_unchanged():
    assert health() == {"status": "ok"}
