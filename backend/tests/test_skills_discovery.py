from io import BytesIO

import pytest
from docx import Document
from fastapi import HTTPException, UploadFile

from app import analysis
from app.analysis import analyze_skills, validate_skills
from app.main import discover_skills, health
from app.resume import validate_and_extract


def docx_bytes(text: str = "Built Python data pipelines and led analytics projects.") -> bytes:
    document = Document()
    document.add_paragraph(text)
    stream = BytesIO()
    document.save(stream)
    return stream.getvalue()


def simple_pdf_bytes(text: str = "Built Python data pipelines") -> bytes:
    stream = f"BT /F1 12 Tf 40 200 Td ({text}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    result = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(result))
        result.extend(f"{number} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(result)
    result.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        result.extend(f"{offset:010d} 00000 n \n".encode())
    result.extend(f"trailer\n<< /Root 1 0 R /Size {len(offsets)} >>\nstartxref\n{xref}\n%%EOF".encode())
    return bytes(result)


def test_rejects_unsupported_type_and_bad_file_contents():
    with pytest.raises(HTTPException) as unsupported:
        validate_and_extract("resume.txt", b"hello", 1000)
    assert unsupported.value.status_code == 415
    with pytest.raises(HTTPException) as invalid:
        validate_and_extract("resume.pdf", b"not a pdf", 1000)
    assert invalid.value.status_code == 400


def test_rejects_empty_and_oversized_files():
    with pytest.raises(HTTPException) as empty:
        validate_and_extract("resume.docx", b"", 1000)
    assert empty.value.status_code == 400
    with pytest.raises(HTTPException) as oversized:
        validate_and_extract("resume.docx", docx_bytes(), 10)
    assert oversized.value.status_code == 413


def test_extracts_docx_text():
    text = validate_and_extract("resume.docx", docx_bytes(), 100_000)
    assert "Built Python data pipelines" in text


def test_extracts_pdf_text():
    text = validate_and_extract("resume.pdf", simple_pdf_bytes(), 100_000)
    assert "Built Python data pipelines" in text


def test_validates_excerpt_and_keeps_inferences_unverified():
    inferred = [{
        "skill_name": "Data analysis", "evidence_type": "inferred",
        "supporting_excerpt": "led analytics projects",
        "relevance_explanation": "Project leadership suggests analytical exposure.",
        "validation_status": "inference_unverified",
    }]
    assert validate_skills(inferred, "Built Python data pipelines and led analytics projects.")[0].validation_status == "inference_unverified"
    inferred[0]["supporting_excerpt"] = "not present"
    with pytest.raises(ValueError):
        validate_skills(inferred, "Built Python data pipelines and led analytics projects.")


def test_configured_provider_response_is_validated_without_network(monkeypatch):
    text = "Built Python data pipelines."
    monkeypatch.setenv("SKILLFAIR_LLM_PROVIDER", "gemini")
    monkeypatch.setenv("SKILLFAIR_LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta")
    monkeypatch.setenv("SKILLFAIR_LLM_API_KEY", "unit-test-key")
    monkeypatch.setenv("SKILLFAIR_LLM_MODEL", "gemini-3.8-flash")

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"candidates": [{"content": {"parts": [{"text": '{"skills":[{"skill_name":"Python","evidence_type":"explicit","supporting_excerpt":"Python","relevance_explanation":"Named directly in the resume.","validation_status":"evidence_found"}]}'}]}}]}

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, headers, json):
            assert url == "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent"
            assert headers["x-goog-api-key"] == "unit-test-key"
            assert "unit-test-key" not in url
            generation_config = json["generationConfig"]
            structured_output = generation_config["responseFormat"]["text"]
            assert structured_output["mimeType"] == "APPLICATION_JSON"
            assert "maxItems" not in structured_output["schema"]["properties"]["skills"]
            assert "temperature" not in generation_config
            assert json["systemInstruction"]["parts"][0]["text"]
            return FakeResponse()

    monkeypatch.setattr(analysis.httpx, "Client", lambda timeout: FakeClient())
    result = analyze_skills(text)
    assert result.analysis_mode == "ai"
    assert result.skills[0].validation_status == "evidence_found"
    assert result.skills[0].model_dump() == {
        "skill_name": "Python",
        "evidence_type": "explicit",
        "supporting_excerpt": "Python",
        "relevance_explanation": "Named directly in the resume.",
        "validation_status": "evidence_found",
    }


def test_excessive_skills_are_rejected_by_application_limit():
    skills = [
        {
            "skill_name": f"Skill {index}",
            "evidence_type": "explicit",
            "supporting_excerpt": "Python",
            "relevance_explanation": "Named directly in the resume.",
            "validation_status": "evidence_found",
        }
        for index in range(61)
    ]

    with pytest.raises(ValueError, match="response limit"):
        validate_skills(skills, "Python")


def test_gemini_configuration_errors_are_clear_and_do_not_call_provider(monkeypatch):
    monkeypatch.setenv("SKILLFAIR_LLM_PROVIDER", "gemini")
    monkeypatch.setenv("SKILLFAIR_LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta")
    monkeypatch.setenv("SKILLFAIR_LLM_MODEL", "gemini-3.8-flash")
    monkeypatch.delenv("SKILLFAIR_LLM_API_KEY", raising=False)
    with pytest.raises(HTTPException) as missing_key:
        analyze_skills("Resume text")
    assert missing_key.value.status_code == 503
    assert "SKILLFAIR_LLM_API_KEY" in missing_key.value.detail

    monkeypatch.setenv("SKILLFAIR_LLM_API_KEY", "unit-test-key")
    monkeypatch.setenv("SKILLFAIR_LLM_BASE_URL", "https://attacker.invalid/v1beta")
    with pytest.raises(HTTPException) as invalid_url:
        analyze_skills("Resume text")
    assert invalid_url.value.status_code == 503
    assert "unit-test-key" not in invalid_url.value.detail


def test_gemini_malformed_response_is_sanitized(monkeypatch):
    monkeypatch.setenv("SKILLFAIR_LLM_PROVIDER", "gemini")
    monkeypatch.setenv("SKILLFAIR_LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta")
    monkeypatch.setenv("SKILLFAIR_LLM_API_KEY", "unit-test-key")
    monkeypatch.setenv("SKILLFAIR_LLM_MODEL", "gemini-3.8-flash")
    monkeypatch.setattr(analysis, "_gemini_request", lambda *args: {"candidates": []})
    with pytest.raises(HTTPException) as malformed:
        analyze_skills("Built Python data pipelines.")
    assert malformed.value.status_code == 502
    assert "unit-test-key" not in malformed.value.detail


def test_gemini_thought_part_is_excluded_from_structured_json(monkeypatch):
    monkeypatch.setenv("SKILLFAIR_LLM_PROVIDER", "gemini")
    monkeypatch.setenv("SKILLFAIR_LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta")
    monkeypatch.setenv("SKILLFAIR_LLM_API_KEY", "unit-test-key")
    monkeypatch.setenv("SKILLFAIR_LLM_MODEL", "gemini-3.6-flash")
    structured_json = (
        '{"skills":[{"skill_name":"Python","evidence_type":"explicit",'
        '"supporting_excerpt":"Python","relevance_explanation":"Named directly.",'
        '"validation_status":"evidence_found"}]}'
    )
    body = {
        "candidates": [{
            "content": {"parts": [
                {"thought": True, "text": "Internal reasoning that is not JSON."},
                {"text": structured_json},
            ]},
            "finishReason": "STOP",
        }]
    }
    monkeypatch.setattr(analysis, "_gemini_request", lambda *args: body)

    result = analyze_skills("Python")

    assert result.analysis_mode == "ai"
    assert [skill.skill_name for skill in result.skills] == ["Python"]


@pytest.mark.parametrize(
    "body",
    [
        {"candidates": []},
        {"candidates": [{"content": {"parts": [{"thought": True, "text": "not user output"}]}}]},
        {"promptFeedback": {"blockReason": "SAFETY"}, "candidates": []},
        {"candidates": [{"content": {"parts": [{"text": "```json {\\\"skills\\\": []} ```"}]}}]},
    ],
)
def test_gemini_rejects_blocked_or_malformed_response_shapes_safely(monkeypatch, body):
    monkeypatch.setenv("SKILLFAIR_LLM_PROVIDER", "gemini")
    monkeypatch.setenv("SKILLFAIR_LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta")
    monkeypatch.setenv("SKILLFAIR_LLM_API_KEY", "unit-test-key")
    monkeypatch.setenv("SKILLFAIR_LLM_MODEL", "gemini-3.6-flash")
    monkeypatch.setattr(analysis, "_gemini_request", lambda *args: body)

    with pytest.raises(HTTPException) as malformed:
        analyze_skills("Python")

    assert malformed.value.status_code == 502
    assert malformed.value.detail == "Gemini returned a malformed or unsupported skills response. Please retry."
    assert "unit-test-key" not in malformed.value.detail


def test_gemini_auth_failure_is_actionable_without_exposing_key(monkeypatch):
    monkeypatch.setenv("SKILLFAIR_LLM_PROVIDER", "gemini")
    monkeypatch.setenv("SKILLFAIR_LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta")
    monkeypatch.setenv("SKILLFAIR_LLM_API_KEY", "unit-test-key")
    monkeypatch.setenv("SKILLFAIR_LLM_MODEL", "gemini-3.8-flash")
    request = analysis.httpx.Request("POST", "https://generativelanguage.googleapis.com/v1beta/test")
    response = analysis.httpx.Response(401, request=request)
    failure = analysis.httpx.HTTPStatusError("unauthorized", request=request, response=response)

    def unauthorized(*args):
        raise failure

    monkeypatch.setattr(analysis, "_gemini_request", unauthorized)
    with pytest.raises(HTTPException) as auth_error:
        analyze_skills("Resume text")
    assert auth_error.value.status_code == 502
    assert "SKILLFAIR_LLM_API_KEY" in auth_error.value.detail
    assert "unit-test-key" not in auth_error.value.detail


def test_gemini_unavailable_retries_once_then_returns_valid_response(monkeypatch):
    monkeypatch.setenv("SKILLFAIR_LLM_PROVIDER", "gemini")
    monkeypatch.setenv("SKILLFAIR_LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta")
    monkeypatch.setenv("SKILLFAIR_LLM_API_KEY", "unit-test-key")
    monkeypatch.setenv("SKILLFAIR_LLM_MODEL", "gemini-3.6-flash")
    request = analysis.httpx.Request("POST", "https://generativelanguage.googleapis.com/v1beta/test")
    unavailable = analysis.httpx.Response(
        503,
        json={"error": {"status": "UNAVAILABLE", "message": "service temporarily unavailable"}},
        request=request,
    )
    failure = analysis.httpx.HTTPStatusError("upstream failure", request=request, response=unavailable)
    valid_body = {
        "candidates": [{"content": {"parts": [{"text": (
            '{"skills":[{"skill_name":"Python","evidence_type":"explicit",'
            '"supporting_excerpt":"Python","relevance_explanation":"Named directly.",'
            '"validation_status":"evidence_found"}]}'
        )}]}}]
    }
    attempts = []

    def unavailable_then_success(*args):
        attempts.append(1)
        if len(attempts) == 1:
            raise failure
        return valid_body

    monkeypatch.setattr(analysis, "_gemini_request", unavailable_then_success)
    monkeypatch.setattr(analysis.time, "sleep", lambda delay: None)

    result = analyze_skills("Python")

    assert len(attempts) == 2
    assert result.analysis_mode == "ai"
    assert result.skills[0].skill_name == "Python"


def test_gemini_unavailable_retry_is_bounded_to_two_attempts(monkeypatch):
    monkeypatch.setenv("SKILLFAIR_LLM_PROVIDER", "gemini")
    monkeypatch.setenv("SKILLFAIR_LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta")
    monkeypatch.setenv("SKILLFAIR_LLM_API_KEY", "unit-test-key")
    monkeypatch.setenv("SKILLFAIR_LLM_MODEL", "gemini-3.6-flash")
    request = analysis.httpx.Request("POST", "https://generativelanguage.googleapis.com/v1beta/test")
    response = analysis.httpx.Response(
        503,
        json={"error": {"status": "UNAVAILABLE", "message": "service temporarily unavailable"}},
        request=request,
    )
    failure = analysis.httpx.HTTPStatusError("upstream failure", request=request, response=response)
    attempts = []

    def always_unavailable(*args):
        attempts.append(1)
        raise failure

    monkeypatch.setattr(analysis, "_gemini_request", always_unavailable)
    monkeypatch.setattr(analysis.time, "sleep", lambda delay: None)

    with pytest.raises(HTTPException) as error:
        analyze_skills("Python")

    assert error.value.status_code == 502
    assert len(attempts) == 2


@pytest.mark.parametrize(
    ("status_code", "provider_status"),
    [(400, "INVALID_ARGUMENT"), (503, "INTERNAL")],
)
def test_gemini_does_not_retry_non_unavailable_http_errors(monkeypatch, status_code, provider_status):
    monkeypatch.setenv("SKILLFAIR_LLM_PROVIDER", "gemini")
    monkeypatch.setenv("SKILLFAIR_LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta")
    monkeypatch.setenv("SKILLFAIR_LLM_API_KEY", "unit-test-key")
    monkeypatch.setenv("SKILLFAIR_LLM_MODEL", "gemini-3.6-flash")
    request = analysis.httpx.Request("POST", "https://generativelanguage.googleapis.com/v1beta/test")
    response = analysis.httpx.Response(
        status_code,
        json={"error": {"status": provider_status, "message": "sanitized test failure"}},
        request=request,
    )
    failure = analysis.httpx.HTTPStatusError("upstream failure", request=request, response=response)
    attempts = []

    def always_fail(*args):
        attempts.append(1)
        raise failure

    monkeypatch.setattr(analysis, "_gemini_request", always_fail)
    monkeypatch.setattr(analysis.time, "sleep", lambda delay: None)

    with pytest.raises(HTTPException) as error:
        analyze_skills("Python")

    assert error.value.status_code == 502
    assert len(attempts) == 1


def test_upstream_error_logging_contains_status_but_not_key_or_resume(monkeypatch, caplog):
    monkeypatch.setenv("SKILLFAIR_LLM_PROVIDER", "gemini")
    monkeypatch.setenv("SKILLFAIR_LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta")
    monkeypatch.setenv("SKILLFAIR_LLM_API_KEY", "unit-test-key")
    monkeypatch.setenv("SKILLFAIR_LLM_MODEL", "gemini-3.8-flash")
    request = analysis.httpx.Request(
        "POST",
        "https://generativelanguage.googleapis.com/v1beta/test",
        headers={"x-goog-api-key": "unit-test-key"},
    )
    response = analysis.httpx.Response(
        400,
        json={"error": {"status": "INVALID_ARGUMENT", "message": "Invalid schema: maxItems 40; candidate resume contents and unit-test-key"}},
        request=request,
    )
    failure = analysis.httpx.HTTPStatusError("upstream failure", request=request, response=response)

    def bad_request(*args):
        raise failure

    monkeypatch.setattr(analysis, "_gemini_request", bad_request)
    with caplog.at_level("WARNING", logger="app.analysis"):
        with pytest.raises(HTTPException) as upstream_error:
            analyze_skills("candidate resume contents")

    assert upstream_error.value.status_code == 502
    assert "upstream_http_status=400" in caplog.text
    assert "provider_status=INVALID_ARGUMENT" in caplog.text
    assert "category=schema_max_items" in caplog.text
    assert "Gemini rejected a structured-output maxItems constraint." in caplog.text
    assert "validation_reason=Gemini rejected a structured-output maxItems constraint." in caplog.text
    assert "unit-test-key" not in caplog.text
    assert "candidate resume contents" not in caplog.text
    assert "Invalid schema" not in caplog.text
    assert "maxItems 40" not in caplog.text
    assert "candidate resume contents" not in upstream_error.value.detail
    assert "unit-test-key" not in upstream_error.value.detail
    assert upstream_error.value.detail == "Gemini rejected the request. Check the model name and structured-output support."


def test_provider_diagnostic_maps_temperature_error_without_echoing_message():
    response = analysis.httpx.Response(
        400,
        json={"error": {"message": "temperature is unsupported; echoed candidate resume and api key value"}},
    )
    diagnostic = analysis._provider_diagnostic_message(response)
    assert diagnostic == "Gemini rejected the temperature generation parameter."
    assert "candidate resume" not in diagnostic
    assert "api key value" not in diagnostic
    assert analysis._provider_error_category(400, diagnostic) == "invalid_generation_parameter"


@pytest.mark.parametrize(
    ("provider_message", "expected_category"),
    [
        ("Schema has too much branching", "schema_complexity"),
        ("Invalid schema: maxItems 40", "schema_max_items"),
        ("Unknown field responseMimeType", "unsupported_request_field"),
        ("Unclassified validation text", "invalid_request_or_schema"),
    ],
)
def test_provider_error_category_uses_sanitized_validation_summary(provider_message, expected_category):
    response = analysis.httpx.Response(400, json={"error": {"message": provider_message}})
    safe_summary = analysis._provider_diagnostic_message(response)

    assert provider_message not in safe_summary
    assert analysis._provider_error_category(400, safe_summary) == expected_category


def test_development_mode_extracts_without_fabricating_skills(monkeypatch):
    monkeypatch.setenv("SKILLFAIR_LLM_PROVIDER", "none")
    result = analyze_skills("Built Python data pipelines.")
    assert result.analysis_mode == "development"
    assert result.skills == []
    assert "no skills have been generated" in result.message


def test_discovery_endpoint_development_flow(monkeypatch):
    monkeypatch.setenv("SKILLFAIR_LLM_PROVIDER", "none")
    upload = UploadFile(file=BytesIO(docx_bytes()), filename="resume.docx")
    body = discover_skills(upload).model_dump()
    assert body["analysis_mode"] == "development"
    assert body["skills"] == []
    assert "Python data pipelines" in body["extracted_text"]


def test_health_endpoint():
    assert health() == {"status": "ok"}
