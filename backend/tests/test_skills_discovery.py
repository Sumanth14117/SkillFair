from io import BytesIO
import json
import struct
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from docx import Document
from fastapi import HTTPException, UploadFile
from pypdf import PdfWriter

from app import analysis
from app.analysis import analyze_skills, validate_skills
from app import main
from app.main import discover_skills, health
from app.models import (
    CareerOrchestrationRequest,
    InclusiveMatchRequest,
    JobMatchRequest,
    SkillEvidence,
    SkillsDiscoverResponse,
)
from app.career_orchestrator import CareerWorkflowStore
from app.local_skills import extract_local_skills
from app.provenance import AnalysisStore
from app.privacy import redact_evidence_excerpt, redact_skill_evidence
from app.resume import validate_and_extract
from app import resume


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


def analyze_ai_skills(text: str) -> SkillsDiscoverResponse:
    return analyze_skills(text, requested_mode="ai", consent_to_gemini=True)


def blank_pdf_bytes(page_count: int) -> bytes:
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=300, height=300)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def zipped_docx_bytes(entries: dict[str, bytes]) -> bytes:
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    return output.getvalue()


def docx_with_total_entries(total_entries: int) -> bytes:
    source_bytes = docx_bytes()
    with ZipFile(BytesIO(source_bytes)) as source:
        parts = [(item.filename, source.read(item)) for item in source.infolist()]
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for name, payload in parts:
            archive.writestr(name, payload)
        for index in range(total_entries - len(parts)):
            archive.writestr(f"custom/part-{index}.bin", b"x")
    return output.getvalue()


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


def test_pdf_page_limit_is_checked_before_any_page_text_extraction(monkeypatch):
    real_reader = resume.PdfReader
    extraction_calls = []

    class ObservedPage:
        def extract_text(self):
            extraction_calls.append(True)
            raise AssertionError("page text extraction must not run after the page limit is exceeded")

    class ObservedReader:
        def __init__(self, stream, strict):
            self._reader = real_reader(stream, strict=strict)
            self.pages = [ObservedPage() for _ in self._reader.pages]

    monkeypatch.setattr(resume, "PdfReader", ObservedReader)
    monkeypatch.setenv("SKILLFAIR_MAX_PDF_PAGES", "1")
    with pytest.raises(HTTPException) as too_many:
        validate_and_extract("resume.pdf", blank_pdf_bytes(2), 100_000)

    assert too_many.value.status_code == 413
    assert too_many.value.detail == "The PDF exceeds the configured 1-page processing limit."
    assert extraction_calls == []


def test_malformed_pdf_returns_safe_error():
    with pytest.raises(HTTPException) as malformed:
        validate_and_extract("resume.pdf", b"%PDF-1.4\nmalformed document", 100_000)
    assert malformed.value.status_code == 400
    assert malformed.value.detail == "The file could not be read. Try exporting the resume as PDF or DOCX."
    assert "malformed document" not in malformed.value.detail


def test_docx_expanded_size_limit_is_checked_before_python_docx(monkeypatch):
    monkeypatch.setattr(resume, "DOCX_MAX_EXPANDED_BYTES", 1024)

    def should_not_parse(*_args, **_kwargs):
        raise AssertionError("python-docx must not run after metadata exceeds the expanded-size limit")

    monkeypatch.setattr(resume, "Document", should_not_parse)
    with pytest.raises(HTTPException) as too_large:
        validate_and_extract("resume.docx", docx_bytes(), 100_000)
    assert too_large.value.status_code == 413
    assert too_large.value.detail == "The DOCX expands beyond the safe processing limit."


def test_docx_entry_count_limit_is_checked_before_python_docx(monkeypatch):
    monkeypatch.setattr(resume, "DOCX_MAX_ENTRIES", 5)
    payload = docx_with_total_entries(6)

    def should_not_allocate_zip_metadata(*_args, **_kwargs):
        raise AssertionError("ZipFile must not allocate per-entry metadata after the count limit is exceeded")

    monkeypatch.setattr(resume, "ZipFile", should_not_allocate_zip_metadata)
    with pytest.raises(HTTPException) as too_many:
        validate_and_extract("resume.docx", payload, 100_000)
    assert too_many.value.status_code == 413
    assert too_many.value.detail == "The DOCX contains too many archive entries to process safely."


def test_docx_actual_entry_count_is_bounded_even_if_eocd_count_is_underreported(monkeypatch):
    monkeypatch.setattr(resume, "DOCX_MAX_ENTRIES", 5)
    payload = bytearray(docx_with_total_entries(6))
    eocd_offset = payload.rfind(b"PK\x05\x06")
    struct.pack_into("<HH", payload, eocd_offset + 8, 1, 1)

    def should_not_allocate_zip_metadata(*_args, **_kwargs):
        raise AssertionError("actual central-directory entries must be counted before ZipFile allocation")

    monkeypatch.setattr(resume, "ZipFile", should_not_allocate_zip_metadata)
    with pytest.raises(HTTPException) as too_many:
        validate_and_extract("resume.docx", bytes(payload), 100_000)
    assert too_many.value.status_code == 413
    assert too_many.value.detail == "The DOCX contains too many archive entries to process safely."


def test_docx_entry_count_just_below_limit_remains_supported(monkeypatch):
    payload = docx_with_total_entries(20)
    monkeypatch.setattr(resume, "DOCX_MAX_ENTRIES", 21)
    text = validate_and_extract("resume.docx", payload, 100_000)
    assert "Python" in text


def test_malformed_docx_central_directory_metadata_returns_safe_error():
    payload = bytearray(docx_bytes())
    eocd_offset = payload.rfind(b"PK\x05\x06")
    assert eocd_offset >= 0
    # Corrupt the central-directory size while leaving the rest of the file intact.
    original_size = struct.unpack_from("<I", payload, eocd_offset + 12)[0]
    struct.pack_into("<I", payload, eocd_offset + 12, original_size + 1)

    with pytest.raises(HTTPException) as malformed:
        validate_and_extract("resume.docx", bytes(payload), 100_000)
    assert malformed.value.status_code == 400
    assert malformed.value.detail == "The file is not a valid, readable PDF or DOCX document."


def test_docx_suspicious_compression_ratio_is_rejected_before_parsing(monkeypatch):
    oversized_ratio = b"<document>" + (b"x" * 250_000) + b"</document>"
    payload = zipped_docx_bytes({"word/document.xml": oversized_ratio})

    def should_not_parse(*_args, **_kwargs):
        raise AssertionError("python-docx must not run for a suspiciously compressed archive")

    monkeypatch.setattr(resume, "Document", should_not_parse)
    with pytest.raises(HTTPException) as suspicious:
        validate_and_extract("resume.docx", payload, 1_000_000)
    assert suspicious.value.status_code == 400
    assert suspicious.value.detail == "The DOCX archive uses suspicious compression and cannot be processed."


def test_malformed_docx_zip_returns_safe_error():
    with pytest.raises(HTTPException) as malformed:
        validate_and_extract("resume.docx", b"PK\x03\x04malformed zip data", 100_000)
    assert malformed.value.status_code == 400
    assert malformed.value.detail == "The file is not a valid, readable PDF or DOCX document."


def test_docx_xml_complexity_limit_is_enforced_before_python_docx(monkeypatch):
    monkeypatch.setattr(resume, "DOCX_MAX_XML_ELEMENTS", 2)
    payload = zipped_docx_bytes({"word/document.xml": b"<document><paragraph><text>resume</text></paragraph></document>"})

    def should_not_parse(*_args, **_kwargs):
        raise AssertionError("python-docx must not run after XML complexity validation fails")

    monkeypatch.setattr(resume, "Document", should_not_parse)
    with pytest.raises(HTTPException) as too_complex:
        validate_and_extract("resume.docx", payload, 100_000)
    assert too_complex.value.status_code == 413
    assert too_complex.value.detail == "The DOCX XML structure exceeds the safe processing limit."


def test_docx_dtd_and_entity_declarations_are_rejected(monkeypatch):
    payload = zipped_docx_bytes({
        "word/document.xml": b'<!DOCTYPE document [<!ENTITY x "expanded">]><document>&x;</document>',
    })

    def should_not_parse(*_args, **_kwargs):
        raise AssertionError("python-docx must not run for XML containing DTD/entity declarations")

    monkeypatch.setattr(resume, "Document", should_not_parse)
    with pytest.raises(HTTPException) as unsafe_xml:
        validate_and_extract("resume.docx", payload, 100_000)
    assert unsafe_xml.value.status_code == 400
    assert unsafe_xml.value.detail == "The file is not a valid, readable PDF or DOCX document."


def test_upload_spool_is_closed_after_success_and_failure(monkeypatch):
    monkeypatch.setenv("SKILLFAIR_LLM_PROVIDER", "none")
    valid_upload = UploadFile(file=BytesIO(docx_bytes("Skills: Python")), filename="resume.docx")
    discover_skills(valid_upload, analysis_mode="local", consent_to_gemini="false")
    assert valid_upload.file.closed

    invalid_upload = UploadFile(file=BytesIO(b"not a resume"), filename="resume.txt")
    with pytest.raises(HTTPException):
        discover_skills(invalid_upload, analysis_mode="local", consent_to_gemini="false")
    assert invalid_upload.file.closed


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

    client_timeouts = []
    monkeypatch.setattr(analysis.httpx, "Client", lambda timeout: (client_timeouts.append(timeout), FakeClient())[1])
    result = analyze_ai_skills(text)
    assert client_timeouts[0].connect == 10
    assert client_timeouts[0].read == 120
    assert client_timeouts[0].write == 10
    assert client_timeouts[0].pool == 10
    assert result.analysis_mode == "ai"
    assert result.skills[0].validation_status == "evidence_found"
    assert result.skills[0].model_dump() == {
        "skill_name": "Python",
        "evidence_type": "explicit",
        "detection_method": "ai",
        "supporting_excerpt": "Python",
        "relevance_explanation": "Named directly in the resume.",
        "validation_status": "evidence_found",
    }


def test_gemini_timeout_settings_are_configurable_and_bounded(monkeypatch):
    monkeypatch.setenv("SKILLFAIR_GEMINI_CONNECT_TIMEOUT_SECONDS", "4.5")
    monkeypatch.setenv("SKILLFAIR_GEMINI_RESPONSE_TIMEOUT_SECONDS", "90")
    timeout = analysis._gemini_timeout()
    assert timeout.connect == 4.5
    assert timeout.read == 90

    monkeypatch.setenv("SKILLFAIR_GEMINI_RESPONSE_TIMEOUT_SECONDS", "301")
    with pytest.raises(HTTPException) as invalid:
        analysis._gemini_timeout()
    assert invalid.value.status_code == 503


@pytest.mark.parametrize(
    ("timeout_error", "expected_category", "expected_message"),
    [
        (analysis.httpx.ConnectTimeout("private resume unit-test-key"), "connect_timeout", "connection timed out"),
        (analysis.httpx.ReadTimeout("private resume unit-test-key"), "response_timeout", "did not return a response"),
    ],
)
def test_gemini_timeout_phase_is_logged_safely(monkeypatch, caplog, timeout_error, expected_category, expected_message):
    monkeypatch.setenv("SKILLFAIR_LLM_PROVIDER", "gemini")
    monkeypatch.setenv("SKILLFAIR_LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta")
    monkeypatch.setenv("SKILLFAIR_LLM_API_KEY", "unit-test-key")
    monkeypatch.setenv("SKILLFAIR_LLM_MODEL", "gemini-3.8-flash")
    monkeypatch.setattr(analysis, "_gemini_request_with_retry", lambda *_args: (_ for _ in ()).throw(timeout_error))

    with pytest.raises(HTTPException) as timed_out:
        analyze_ai_skills("Private synthetic resume content")

    assert timed_out.value.status_code == 504
    assert expected_message in timed_out.value.detail
    assert expected_category in caplog.text
    assert "unit-test-key" not in caplog.text
    assert "Private synthetic resume content" not in caplog.text
    assert "private resume" not in caplog.text


def test_gemini_requires_consent_before_provider_path(monkeypatch):
    monkeypatch.setenv("SKILLFAIR_LLM_PROVIDER", "gemini")
    monkeypatch.setattr(analysis, "provider_settings", lambda: (_ for _ in ()).throw(AssertionError("provider must not be consulted before consent")))
    monkeypatch.setattr(analysis, "_gemini_request_with_retry", lambda *_args: (_ for _ in ()).throw(AssertionError("provider request must not run")))
    with pytest.raises(HTTPException) as rejected:
        analyze_skills("Private resume content", requested_mode="ai", consent_to_gemini=False)
    assert rejected.value.status_code == 400
    assert "consent" in rejected.value.detail.lower()
    assert "Private resume content" not in rejected.value.detail


def test_local_mode_never_uses_configured_gemini_or_requires_consent(monkeypatch):
    monkeypatch.setenv("SKILLFAIR_LLM_PROVIDER", "gemini")
    monkeypatch.setattr(analysis, "provider_settings", lambda: (_ for _ in ()).throw(AssertionError("local mode must not inspect provider configuration")))
    monkeypatch.setattr(analysis, "_gemini_request_with_retry", lambda *_args: (_ for _ in ()).throw(AssertionError("local mode must not call Gemini")))
    result = analyze_skills("Skills\nPython", requested_mode="local", consent_to_gemini=False)
    assert result.analysis_mode == "local"
    assert [skill.skill_name for skill in result.skills] == ["Python"]


def test_ai_mode_without_provider_is_unavailable_and_does_not_fall_back(monkeypatch):
    monkeypatch.setenv("SKILLFAIR_LLM_PROVIDER", "none")
    monkeypatch.setattr(analysis, "_gemini_request_with_retry", lambda *_args: (_ for _ in ()).throw(AssertionError("no provider request is possible")))
    with pytest.raises(HTTPException) as unavailable:
        analyze_skills("Skills\nPython", requested_mode="ai", consent_to_gemini=True)
    assert unavailable.value.status_code == 503
    assert "unavailable" in unavailable.value.detail.lower()


def test_gemini_consent_is_per_analysis_request(monkeypatch):
    monkeypatch.setenv("SKILLFAIR_LLM_PROVIDER", "gemini")
    monkeypatch.setattr(analysis, "provider_settings", lambda: ("https://configured.invalid", "private-test-key", "test-model"))
    body = {"candidates": [{"content": {"parts": [{"text": '{"skills":[]}'}]}}]}
    calls = []
    monkeypatch.setattr(analysis, "_gemini_request_with_retry", lambda *_args: (calls.append(True), body)[1])
    first = analyze_skills("Resume", requested_mode="ai", consent_to_gemini=True)
    assert first.analysis_mode == "ai"
    with pytest.raises(HTTPException) as second:
        analyze_skills("Resume", requested_mode="ai", consent_to_gemini=False)
    assert second.value.status_code == 400
    assert calls == [True]


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
        analyze_ai_skills("Resume text")
    assert missing_key.value.status_code == 503
    assert "SKILLFAIR_LLM_API_KEY" in missing_key.value.detail

    monkeypatch.setenv("SKILLFAIR_LLM_API_KEY", "unit-test-key")
    monkeypatch.setenv("SKILLFAIR_LLM_BASE_URL", "https://attacker.invalid/v1beta")
    with pytest.raises(HTTPException) as invalid_url:
        analyze_ai_skills("Resume text")
    assert invalid_url.value.status_code == 503
    assert "unit-test-key" not in invalid_url.value.detail


def test_gemini_malformed_response_is_sanitized(monkeypatch):
    monkeypatch.setenv("SKILLFAIR_LLM_PROVIDER", "gemini")
    monkeypatch.setenv("SKILLFAIR_LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta")
    monkeypatch.setenv("SKILLFAIR_LLM_API_KEY", "unit-test-key")
    monkeypatch.setenv("SKILLFAIR_LLM_MODEL", "gemini-3.8-flash")
    monkeypatch.setattr(analysis, "_gemini_request", lambda *args: {"candidates": []})
    with pytest.raises(HTTPException) as malformed:
        analyze_ai_skills("Built Python data pipelines.")
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

    result = analyze_ai_skills("Python")

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
        analyze_ai_skills("Python")

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
        analyze_ai_skills("Resume text")
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

    result = analyze_ai_skills("Python")

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
        analyze_ai_skills("Python")

    assert error.value.status_code == 502
    assert len(attempts) == 2
    assert error.value.detail == "Gemini is temporarily unavailable. Please retry shortly or choose local analysis."


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
        analyze_ai_skills("Python")

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
            analyze_ai_skills("candidate resume contents")

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


def test_local_mode_extracts_explicit_contextual_mentions(monkeypatch):
    monkeypatch.setenv("SKILLFAIR_LLM_PROVIDER", "none")
    text = "Technical Skills: Python, SQL\nExperience\nBuilt reports using Python and SQL."
    result = analyze_skills(text)
    assert result.analysis_mode == "local"
    assert [skill.skill_name for skill in result.skills] == ["Python", "SQL"]
    assert all(skill.evidence_type == "explicit" for skill in result.skills)
    assert all(skill.detection_method == "local" for skill in result.skills)
    assert all("not proof of proficiency" in skill.relevance_explanation for skill in result.skills)
    assert result.extracted_characters == len(text)
    assert "extracted_text" not in result.model_dump()


def test_local_skill_vocabulary_matches_punctuation_and_case_exactly():
    text = "Skills\nC, C++, C#, c, c++, c#, PYTHON, sql, JavaScript"
    skills = extract_local_skills(text)
    names = [skill.skill_name for skill in skills]
    assert names == ["Python", "C", "C++", "C#", "JavaScript", "SQL"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Skills: C++", ["C++"]),
        ("Skills: C#", ["C#"]),
        ("Skills: C", ["C"]),
        ("Skills: JavaScript", ["JavaScript"]),
        ("Skills: Java", ["Java"]),
    ],
)
def test_local_skill_detector_keeps_symbol_and_name_collisions_distinct(text, expected):
    assert [skill.skill_name for skill in extract_local_skills(text)] == expected


@pytest.mark.parametrize("text", [
    "Technical Skills\nPythonic, NoSQL, CSharp, See#",
    "References\nPython and SQL are listed in this unrelated job board boilerplate.",
    "Objective\nInterested in Python and SQL.",
])
def test_local_skill_detector_avoids_substrings_and_unrelated_mentions(text):
    assert extract_local_skills(text) == []


def test_local_skill_detector_accepts_clear_usage_context_and_exact_excerpt():
    text = "Project: Built reporting scripts using python and SQL to validate records."
    skills = extract_local_skills(text)
    assert [skill.skill_name for skill in skills] == ["Python", "SQL"]
    for skill in skills:
        assert skill.supporting_excerpt == text
        assert skill.supporting_excerpt in text
        assert skill.evidence_type == "explicit"
        assert skill.detection_method == "local"
        assert skill.validation_status == "evidence_found"


def test_local_evidence_preserves_full_source_line_and_attributes_resume_sections():
    text = "\n".join([
        "Programming Languages",
        "Python, C++",
        "Frontend",
        "React.js — Built responsive candidate review screens.",
        "Backend",
        "Node.js, Express.js",
        "Databases",
        "PostgreSQL, MongoDB",
    ])
    by_name = {skill.skill_name: skill for skill in extract_local_skills(text)}

    assert by_name["React"].supporting_excerpt == "React.js — Built responsive candidate review screens."
    assert "Frontend section" in by_name["React"].relevance_explanation
    assert by_name["Node.js"].supporting_excerpt == "Node.js, Express.js"
    assert "Backend section" in by_name["Node.js"].relevance_explanation
    assert by_name["Express"].supporting_excerpt == "Node.js, Express.js"
    assert "Backend section" in by_name["Express"].relevance_explanation
    assert "Programming Languages section" in by_name["Python"].relevance_explanation
    assert "Programming Languages section" in by_name["C++"].relevance_explanation
    assert "Databases section" in by_name["PostgreSQL"].relevance_explanation
    assert "Databases section" in by_name["MongoDB"].relevance_explanation


def test_pypdf_style_two_column_resume_lines_are_not_merged_across_columns():
    # Mirrors the uploaded PDF's default pypdf text output: its two-column skill
    # section emits each bullet as a separate extracted line.
    extracted_pdf_text = "\n".join([
        "Programming Languages",
        "�Python",
        "�C++",
        "Frontend",
        "�React.js",
        "Backend Development",
        "�Node.js",
        "�Express.js",
        "Databases",
        "�MySQL",
        "�MongoDB",
        "�PostgreSQL",
    ])
    by_name = {skill.skill_name: skill for skill in extract_local_skills(extracted_pdf_text)}

    assert by_name["React"].supporting_excerpt == "�React.js"
    assert "Frontend section" in by_name["React"].relevance_explanation
    assert by_name["Node.js"].supporting_excerpt == "�Node.js"
    assert "Backend section" in by_name["Node.js"].relevance_explanation
    assert by_name["Express"].supporting_excerpt == "�Express.js"
    assert "Backend section" in by_name["Express"].relevance_explanation
    assert by_name["Python"].supporting_excerpt == "�Python"
    assert "Programming Languages section" in by_name["Python"].relevance_explanation
    assert by_name["C++"].supporting_excerpt == "�C++"
    assert "Programming Languages section" in by_name["C++"].relevance_explanation
    for name in ("MySQL", "MongoDB", "PostgreSQL"):
        assert "Databases section" in by_name[name].relevance_explanation
    # The adjacent-column SQL text in the visual PDF layout is not attached to React.
    assert "SQL" not in by_name["React"].supporting_excerpt


def test_database_section_does_not_leak_into_internship_and_later_sections():
    text = "\n".join([
        "Databases",
        "•MySQL",
        "•PostgreSQL",
        "Internship Experience",
        "Machine Learning Internship – FlyRank, 2026",
        "Projects",
        "Built reports using Python.",
        "Certifications",
        "Proficient in SQL.",
    ])
    by_name = {skill.skill_name: skill for skill in extract_local_skills(text)}

    assert "Databases section" in by_name["MySQL"].relevance_explanation
    assert "Databases section" in by_name["PostgreSQL"].relevance_explanation
    assert by_name["Machine Learning"].supporting_excerpt == "Machine Learning Internship – FlyRank, 2026"
    assert "Internship Experience section" in by_name["Machine Learning"].relevance_explanation
    assert "Projects section" in by_name["Python"].relevance_explanation
    assert by_name["SQL"].supporting_excerpt == "Proficient in SQL."
    assert "Certifications section" in by_name["SQL"].relevance_explanation


@pytest.mark.parametrize("heading", ["Projects", "Academic Projects", "Personal Projects", "Relevant Projects"])
def test_project_section_detects_exact_technology_lists_without_usage_phrases(heading):
    line = "Task planning dashboard — React, Node.js and SQLite"
    by_name = {skill.skill_name: skill for skill in extract_local_skills(f"{heading}\n{line}")}

    assert set(by_name) == {"React", "Node.js", "SQLite"}
    for skill in by_name.values():
        assert skill.supporting_excerpt == line
        assert "Projects section" in skill.relevance_explanation
        assert skill.evidence_type == "explicit"
        assert skill.validation_status == "evidence_found"
        assert skill.detection_method == "local"
        assert "not proof of proficiency" in skill.relevance_explanation


def test_project_only_skill_and_multiple_technologies_are_detected_with_exact_line():
    line = "• Expense tracker stack: TypeScript, PostgreSQL, Docker"
    by_name = {skill.skill_name: skill for skill in extract_local_skills(f"Projects\n{line}")}

    assert set(by_name) == {"TypeScript", "PostgreSQL", "Docker"}
    assert all(skill.supporting_excerpt == line for skill in by_name.values())
    assert all("Projects section" in skill.relevance_explanation for skill in by_name.values())


def test_project_skill_list_is_distinct_from_title_and_unrelated_prose():
    text = "\n".join([
        "Projects",
        "Python Inventory Dashboard",  # A title alone is not evidence.
        "• React, Node.js, SQLite",  # A technology list within a project entry is evidence.
        "References",
        "Python and SQL appear in this unrelated boilerplate.",
    ])
    by_name = {skill.skill_name: skill for skill in extract_local_skills(text)}

    assert set(by_name) == {"React", "Node.js", "SQLite"}
    assert all("Projects section" in skill.relevance_explanation for skill in by_name.values())


def test_project_detection_preserves_language_symbol_boundaries():
    line = "Calculator project — C, C++, C#, JavaScript"
    names = {skill.skill_name for skill in extract_local_skills(f"Projects\n{line}")}
    assert names == {"C", "C++", "C#", "JavaScript"}


def test_skills_section_evidence_wins_over_project_and_first_project_duplicate_is_stable():
    skills_line = "Python, React"
    first_project = "Portfolio site — React, Node.js"
    second_project = "Reporting console — React, SQL"
    by_name = {
        skill.skill_name: skill
        for skill in extract_local_skills(
            f"Skills\n{skills_line}\nProjects\n{first_project}\n{second_project}"
        )
    }

    assert by_name["React"].supporting_excerpt == skills_line
    assert "Skills section" in by_name["React"].relevance_explanation
    assert by_name["Node.js"].supporting_excerpt == first_project
    assert by_name["SQL"].supporting_excerpt == second_project


def test_project_records_flow_through_existing_provenance_matching_and_orchestrator(monkeypatch):
    excerpt = "Issue tracker — React, Node.js and SQLite"
    skills = extract_local_skills(f"Projects\n{excerpt}")
    store = AnalysisStore()
    analysis_id = store.create(skills, "local")
    monkeypatch.setattr(main, "analysis_store", store)
    monkeypatch.setattr(main, "career_workflow_store", CareerWorkflowStore())

    matched = main.match_job(JobMatchRequest(
        analysis_id=analysis_id,
        job_title="Application Developer",
        job_description="Build and maintain an application.",
        required_skills=["SQLite"],
    ))
    assert matched.matched_skills[0].supporting_excerpt == excerpt

    inclusive = main.inclusive_match(InclusiveMatchRequest(
        analysis_id=analysis_id,
        job_title="Application Developer",
        job_description="Build and maintain an application.",
        essential_skills=["React"],
        preferred_skills=[],
    ))
    assert inclusive.requirements[0].evidence[0].supporting_excerpt == excerpt

    workflow = main.create_career_workflow(CareerOrchestrationRequest(
        analysis_id=analysis_id,
        job_title="Application Developer",
        job_description="Build and maintain an application.",
        essential_skills=["Node.js"],
        preferred_skills=[],
    ))
    assert {skill.skill_name for skill in workflow.skill_passport.skills} == {"React", "Node.js", "SQLite"}
    assert workflow.inclusive_matching.requirements[0].evidence[0].supporting_excerpt == excerpt


def test_actual_resume_page_transition_and_entry_lines_are_attributed_without_stale_state():
    # Relevant lines copied from the resume's pypdf extraction; unrelated personal
    # details are intentionally omitted from this fixture.
    extracted_text = "\n".join([
        "PROFESSIONAL EXPERIENCE",
        "Machine Learning Intern",
        "FlyRank",
        "� Worked on machine learning tasks involving data preprocessing and feature ",
        "Databases",
        "�MySQL",
        "�MongoDB",
        "�PostgreSQL",
        "CERTIFICATES",
        "Machine Learning Internship � FlyRank,2026",
        "Rapid Prototyping & Basics of loT internship - ",
        "COMEDKARES INNOVATION HUB",
        "Deep Learning � INFOSYS SPRINGBOARD",
    ])
    by_name = {skill.skill_name: skill for skill in extract_local_skills(extracted_text)}

    assert by_name["Machine Learning"].supporting_excerpt == "Machine Learning Internship � FlyRank,2026"
    assert "Internship Experience section" in by_name["Machine Learning"].relevance_explanation
    assert by_name["Deep Learning"].supporting_excerpt == "Deep Learning � INFOSYS SPRINGBOARD"
    assert "Certifications section" in by_name["Deep Learning"].relevance_explanation
    for name in ("MySQL", "MongoDB", "PostgreSQL"):
        assert "Databases section" in by_name[name].relevance_explanation


def test_section_parser_normalizes_bullet_dash_and_fullwidth_colon_decorations():
    text = "  • Databases —  \n�MySQL\n\n▪ CERTIFICATES ：\nDeep Learning � INFOSYS SPRINGBOARD"
    by_name = {skill.skill_name: skill for skill in extract_local_skills(text)}
    assert "Databases section" in by_name["MySQL"].relevance_explanation
    assert "Certifications section" in by_name["Deep Learning"].relevance_explanation


def test_unknown_colon_heading_resets_active_list_section():
    text = "Databases\n•MySQL\nAdditional Information:\nWorked with Machine Learning."
    result = {skill.skill_name: skill for skill in extract_local_skills(text)}
    assert "Databases section" in result["MySQL"].relevance_explanation
    assert "resume text" in result["Machine Learning"].relevance_explanation.casefold()
    assert "Databases section" not in result["Machine Learning"].relevance_explanation


@pytest.mark.parametrize(
    ("heading", "expected_section"),
    [
        ("Professional Experience", "Professional Experience"),
        ("Work Experience", "Professional Experience"),
        ("Internship Experience", "Internship Experience"),
        ("Internships", "Internship Experience"),
        ("Experience", "Professional Experience"),
    ],
)
def test_experience_heading_variants_set_section_context(heading, expected_section):
    skill = extract_local_skills(f"{heading}\nWorked with Machine Learning in projects.")[0]
    assert skill.skill_name == "Machine Learning"
    assert expected_section in skill.relevance_explanation


def test_ordinary_experience_sentence_is_not_misread_as_heading_or_inherit_database_section():
    text = "Databases\nMySQL\nExperience in Machine Learning projects is preferred."
    skill = extract_local_skills(text)[0]
    assert skill.skill_name == "Machine Learning"
    assert "resume text" in skill.relevance_explanation.casefold()
    assert "Databases section" not in skill.relevance_explanation


def test_unknown_section_does_not_receive_a_guessed_attribution():
    text = "Special Interests\nBuilt dashboards using React.js."
    skill = extract_local_skills(text)[0]
    assert skill.skill_name == "React"
    assert skill.supporting_excerpt == "Built dashboards using React.js."
    assert "resume text" in skill.relevance_explanation
    assert "section could not be confidently identified" in skill.relevance_explanation


def test_local_detector_omits_overlong_lines_instead_of_truncating_evidence():
    long_line = "Technical Skills: " + ("X" * 700) + " Python"
    assert extract_local_skills(long_line) == []


def test_local_detection_never_emits_inferred_skills():
    skills = extract_local_skills("Skills: Python\nExperience: Worked with SQL.")
    assert skills
    assert all(skill.evidence_type != "inferred" for skill in skills)


def test_local_mode_empty_scan_is_distinct_from_unperformed_analysis(monkeypatch):
    monkeypatch.setenv("SKILLFAIR_LLM_PROVIDER", "none")
    result = analyze_skills("Candidate biography with no vocabulary skill terms.")
    assert result.analysis_mode == "local"
    assert result.skills == []
    assert "does not mean the resume contains no skills" in result.message


@pytest.mark.parametrize(
    ("excerpt", "expected"),
    [
        ("Python engineer · sam@example.com", "Python engineer · [REDACTED EMAIL]"),
        ("Call +1 (415) 555-0132 about the SQL role", "Call [REDACTED PHONE] about the SQL role"),
        ("Portfolio https://example.test/u?email=sam%40example.com", "Portfolio [REDACTED PERSONAL URL]"),
        ("Built Python services; contact sam@example.com", "Built Python services; contact [REDACTED EMAIL]"),
        ("Used React and PostgreSQL in a project.", "Used React and PostgreSQL in a project."),
    ],
)
def test_evidence_pii_redaction_is_conservative_and_preserves_technical_context(excerpt, expected):
    assert redact_evidence_excerpt(excerpt) == expected


def test_skill_evidence_redacts_pii_from_explanation_and_excerpt_but_keeps_label():
    skill = SkillEvidence(
        skill_name="Python",
        evidence_type="explicit",
        supporting_excerpt="Built Python tools; sam@example.test",
        relevance_explanation="Contact sam@example.test or call +1 (415) 555-0132; profile https://example.test/?username=sam",
        validation_status="evidence_found",
    )
    safe = redact_skill_evidence(skill)
    assert safe.skill_name == "Python"
    assert safe.supporting_excerpt == "Built Python tools; [REDACTED EMAIL]"
    assert safe.relevance_explanation == (
        "Contact [REDACTED EMAIL] or call [REDACTED PHONE]; profile [REDACTED PERSONAL URL]"
    )
    serialized = safe.model_dump_json()
    assert "sam@example.test" not in serialized
    assert "415) 555-0132" not in serialized
    assert "username=sam" not in serialized


@pytest.mark.parametrize("skill_name", [
    "sam@example.test",
    "Python +1 (415) 555-0132",
    "https://example.test/in/sam",
    "https://example.test/?email=sam%40example.test",
])
def test_skill_name_with_contact_pii_or_url_is_rejected(skill_name):
    skill = SkillEvidence(
        skill_name=skill_name,
        evidence_type="explicit",
        supporting_excerpt=f"Mentioned {skill_name}",
        relevance_explanation=f"Contact {skill_name}",
        validation_status="evidence_found",
    )
    with pytest.raises(ValueError, match="skill name contains contact information or a URL"):
        redact_skill_evidence(skill)


@pytest.mark.parametrize("skill_name", [
    "C", "C++", "C#", "Java", "JavaScript", "Python", "SQL", "React", "Node.js", "TypeScript",
])
def test_redaction_preserves_legitimate_technical_skill_labels(skill_name):
    skill = SkillEvidence(
        skill_name=skill_name,
        evidence_type="explicit",
        supporting_excerpt=f"Used {skill_name} in a project.",
        relevance_explanation=f"The resume mentions {skill_name}.",
        validation_status="evidence_found",
    )
    assert redact_skill_evidence(skill).skill_name == skill_name


def test_language_labels_remain_distinct_after_skill_evidence_sanitization():
    labels = ["C", "C++", "C#", "Java", "JavaScript"]
    sanitized = [redact_skill_evidence(SkillEvidence(
        skill_name=label,
        evidence_type="explicit",
        supporting_excerpt=f"Used {label}.",
        relevance_explanation=f"Mentioned {label}.",
        validation_status="evidence_found",
    )).skill_name for label in labels]
    assert sanitized == labels
    assert len(set(sanitized)) == len(labels)


def test_ai_validation_rejects_pii_in_skill_name_without_echoing_it():
    private_label = "sam@example.test"
    with pytest.raises(ValueError, match="skill name contains contact information or a URL"):
        validate_skills([{
            "skill_name": private_label,
            "evidence_type": "explicit",
            "supporting_excerpt": private_label,
            "relevance_explanation": f"Contact {private_label}",
            "validation_status": "evidence_found",
        }], f"Skills: {private_label}")


def test_ai_analysis_redacts_excerpt_and_explanation_before_returning_response(monkeypatch):
    resume_text = "Built Python tools; contact sam@example.test"
    raw_skill = {
        "skill_name": "Python",
        "evidence_type": "explicit",
        "supporting_excerpt": resume_text,
        "relevance_explanation": "Contact sam@example.test about Python work at https://example.test/?phone=4155550132",
        "validation_status": "evidence_found",
    }
    monkeypatch.setattr(analysis, "provider_settings", lambda: ("https://configured.invalid", "private-test-key", "test-model"))
    monkeypatch.setattr(analysis, "_gemini_request_with_retry", lambda *_args: {
        "candidates": [{"content": {"parts": [{"text": json.dumps({"skills": [raw_skill]})}]}}],
    })

    result = analyze_ai_skills(resume_text)

    assert result.analysis_mode == "ai"
    assert result.skills[0].skill_name == "Python"
    assert result.skills[0].supporting_excerpt == "Built Python tools; contact [REDACTED EMAIL]"
    assert result.skills[0].relevance_explanation == (
        "Contact [REDACTED EMAIL] about Python work at [REDACTED PERSONAL URL]"
    )
    assert "sam@example.test" not in result.model_dump_json()
    assert "4155550132" not in result.model_dump_json()


def test_ai_analysis_rejects_pii_skill_label_with_safe_provider_error(monkeypatch):
    private_label = "sam@example.test"
    resume_text = f"Skills: {private_label}"
    raw_skill = {
        "skill_name": private_label,
        "evidence_type": "explicit",
        "supporting_excerpt": resume_text,
        "relevance_explanation": f"Contact {private_label}",
        "validation_status": "evidence_found",
    }
    monkeypatch.setattr(analysis, "provider_settings", lambda: ("https://configured.invalid", "private-test-key", "test-model"))
    monkeypatch.setattr(analysis, "_gemini_request_with_retry", lambda *_args: {
        "candidates": [{"content": {"parts": [{"text": json.dumps({"skills": [raw_skill]})}]}}],
    })

    with pytest.raises(HTTPException) as rejected:
        analyze_ai_skills(resume_text)
    assert rejected.value.status_code == 502
    assert private_label not in rejected.value.detail


def test_local_skill_labels_and_explanations_remain_unchanged_after_pii_protection():
    result = analyze_skills("Programming Languages\nPython, C, C++, C#, Java, JavaScript")
    assert [item.skill_name for item in result.skills] == ["Python", "Java", "C", "C++", "C#", "JavaScript"]
    assert all("mention is not proof of proficiency" in item.relevance_explanation for item in result.skills)


def test_local_discovery_redacts_pii_before_return_and_provenance(monkeypatch):
    monkeypatch.setenv("SKILLFAIR_LLM_PROVIDER", "none")
    raw_excerpt = "Python engineer · sam@example.com · +1 (415) 555-0132"
    response = analyze_skills(f"Skills\n{raw_excerpt}")
    assert response.skills[0].supporting_excerpt == "Python engineer · [REDACTED EMAIL] · [REDACTED PHONE]"
    assert "sam@example.com" not in response.model_dump_json()
    assert "415) 555-0132" not in response.model_dump_json()

    store = AnalysisStore()
    # Storage is a second enforcement boundary for records created outside analyze_skills.
    raw_skill = SkillEvidence(
        skill_name="Python", evidence_type="explicit", supporting_excerpt=raw_excerpt,
        relevance_explanation="Mentioned in a skills section.", validation_status="evidence_found", detection_method="local",
    )
    analysis_id = store.create([raw_skill], "local")
    stored = store.resolve(analysis_id)
    assert stored is not None
    assert stored[0].supporting_excerpt == response.skills[0].supporting_excerpt
    assert stored[0].skill_name == "Python"
    assert stored[0].relevance_explanation == "Mentioned in a skills section."


def test_discovery_endpoint_local_flow_stores_mode_and_validated_skills(monkeypatch):
    monkeypatch.setenv("SKILLFAIR_LLM_PROVIDER", "none")
    store = AnalysisStore()
    monkeypatch.setattr(main, "analysis_store", store)
    upload = UploadFile(file=BytesIO(docx_bytes("Technical Skills: Python, SQL")), filename="resume.docx")
    body = discover_skills(upload, analysis_mode="local", consent_to_gemini="false").model_dump()
    assert body["analysis_mode"] == "local"
    assert [item["skill_name"] for item in body["skills"]] == ["Python", "SQL"]
    assert isinstance(body["analysis_id"], str)
    resolved = store.resolve_with_mode(body["analysis_id"])
    assert resolved is not None
    assert [skill.skill_name for skill in resolved[0]] == ["Python", "SQL"]
    assert all(skill.detection_method == "local" for skill in resolved[0])
    assert resolved[1] == "local"
    assert body["extracted_characters"] > 0
    assert "extracted_text" not in body


@pytest.mark.parametrize("consent", [None, "false"])
def test_discovery_endpoint_rejects_ai_upload_without_consent_before_extraction(monkeypatch, consent):
    upload = UploadFile(file=BytesIO(docx_bytes("Skills: Python")), filename="resume.docx")
    monkeypatch.setattr(main, "validate_and_extract", lambda *_args: (_ for _ in ()).throw(AssertionError("extracting must wait for consent")))
    monkeypatch.setattr(main, "analyze_skills", lambda *_args: (_ for _ in ()).throw(AssertionError("analysis must wait for consent")))
    with pytest.raises(HTTPException) as rejected:
        discover_skills(upload, analysis_mode="ai", consent_to_gemini=consent)
    assert rejected.value.status_code == 400
    assert "consent" in rejected.value.detail.lower()
    assert upload.file.closed


@pytest.mark.parametrize("value", ["yes", "1", "True", "granted"])
def test_discovery_endpoint_rejects_malformed_consent_value(value):
    upload = UploadFile(file=BytesIO(docx_bytes("Skills: Python")), filename="resume.docx")
    with pytest.raises(HTTPException) as rejected:
        discover_skills(upload, analysis_mode="ai", consent_to_gemini=value)
    assert rejected.value.status_code == 422
    assert "invalid" in rejected.value.detail.lower()


def test_successful_discovery_returns_identifier_for_stored_validated_skills(monkeypatch):
    skills = [SkillEvidence.model_validate({
        "skill_name": "Python",
        "evidence_type": "explicit",
        "supporting_excerpt": "Python",
        "relevance_explanation": "Named directly in the resume.",
        "validation_status": "evidence_found",
    })]
    monkeypatch.setattr(main, "analysis_store", AnalysisStore())
    monkeypatch.setattr(main, "analyze_skills", lambda text, requested_mode, consent: SkillsDiscoverResponse(
        analysis_mode="ai",
        message="AI analysis complete.",
        extracted_characters=len(text),
        skills=skills,
    ))

    upload = UploadFile(file=BytesIO(docx_bytes("Python")), filename="resume.docx")
    response = discover_skills(upload, analysis_mode="ai", consent_to_gemini="true")

    assert response.analysis_id
    assert main.analysis_store.resolve(response.analysis_id) == skills
    assert "extracted_text" not in response.model_dump()


def test_health_endpoint():
    assert health() == {"status": "ok"}


def groq_response_body(content: str | None = None, *, refusal: str | None = None) -> dict:
    return {"choices": [{"message": {"content": content, "refusal": refusal}, "finish_reason": "stop"}]}


def test_groq_provider_selection_requires_its_separate_key(monkeypatch):
    monkeypatch.setenv("SKILLFAIR_LLM_PROVIDER", "groq")
    monkeypatch.delenv("SKILLFAIR_GROQ_API_KEY", raising=False)
    monkeypatch.setenv("SKILLFAIR_LLM_API_KEY", "gemini-key-must-not-be-reused")

    with pytest.raises(HTTPException) as missing_key:
        analyze_skills("Python", requested_mode="ai", consent_to_ai_provider=True)

    assert missing_key.value.status_code == 503
    assert "SKILLFAIR_GROQ_API_KEY" in missing_key.value.detail
    assert "gemini-key-must-not-be-reused" not in missing_key.value.detail


def test_groq_request_uses_fixed_endpoint_bearer_auth_and_strict_schema(monkeypatch):
    monkeypatch.setenv("SKILLFAIR_LLM_PROVIDER", "groq")
    monkeypatch.setenv("SKILLFAIR_GROQ_API_KEY", "groq-unit-test-secret")
    monkeypatch.setenv("SKILLFAIR_GROQ_MODEL", "openai/gpt-oss-20b")
    monkeypatch.setenv("SKILLFAIR_GROQ_MAX_COMPLETION_TOKENS", "2048")
    expected = {"skills": [{
        "skill_name": "Python",
        "evidence_type": "explicit",
        "evidence_line_id": 1,
        "relevance_explanation": "Python is named in the work example.",
        "validation_status": "evidence_found",
    }]}
    response_body = groq_response_body(json.dumps(expected))
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return response_body

    class FakeClient:
        def __init__(self, timeout):
            captured["timeout"] = timeout

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, endpoint, *, headers, json):
            captured.update(endpoint=endpoint, headers=headers, payload=json)
            return FakeResponse()

    monkeypatch.setattr(analysis.httpx, "Client", FakeClient)
    result = analyze_skills("Built Python data pipelines.", requested_mode="ai", consent_to_ai_provider=True)

    assert captured["endpoint"] == "https://api.groq.com/openai/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer groq-unit-test-secret"
    assert "groq-unit-test-secret" not in captured["endpoint"]
    assert captured["payload"]["model"] == "openai/gpt-oss-20b"
    assert captured["payload"]["max_completion_tokens"] == 2048
    response_format = captured["payload"]["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    schema = response_format["json_schema"]["schema"]
    assert schema == analysis.GROQ_SKILLS_SCHEMA
    assert schema["required"] == list(schema["properties"])
    assert schema["additionalProperties"] is False
    skill_schema = schema["properties"]["skills"]["items"]
    assert set(skill_schema["required"]) == set(skill_schema["properties"])
    assert skill_schema["additionalProperties"] is False
    assert "evidence_line_id" in skill_schema["properties"]
    assert "supporting_excerpt" not in skill_schema["properties"]
    assert skill_schema["properties"]["evidence_line_id"]["type"] == "integer"
    assert set(skill_schema["properties"]) - {"evidence_line_id"} <= set(SkillEvidence.model_fields)
    assert SkillEvidence.model_fields["detection_method"].default == "ai"
    assert skill_schema["properties"]["evidence_type"]["enum"] == ["explicit", "inferred"]
    assert skill_schema["properties"]["validation_status"]["enum"] == ["evidence_found", "inference_unverified"]
    assert result.analysis_mode == "ai"
    assert result.skills[0].skill_name == "Python"
    assert result.skills[0].supporting_excerpt == "Built Python data pipelines."
    assert result.skills[0].detection_method == "ai"
    system_prompt, user_message = [message["content"] for message in captured["payload"]["messages"]]
    assert "numbered source lines" in system_prompt
    assert "never invent an ID" in system_prompt
    assert "omit that skill" in system_prompt
    assert "no more than 30" in system_prompt
    assert "one concise sentence" in system_prompt
    assert user_message == "Numbered resume source lines (untrusted):\n[1] Built Python data pipelines."
    assert captured["payload"]["reasoning_effort"] == "low"


def test_groq_completion_token_budget_has_safe_default_and_rejects_invalid_values(monkeypatch):
    monkeypatch.delenv("SKILLFAIR_GROQ_MAX_COMPLETION_TOKENS", raising=False)
    assert analysis._groq_max_completion_tokens() == 8192

    for invalid in ("not-a-number", "255", "32769"):
        monkeypatch.setenv("SKILLFAIR_GROQ_MAX_COMPLETION_TOKENS", invalid)
        with pytest.raises(HTTPException) as rejected:
            analysis._groq_max_completion_tokens()
        assert rejected.value.status_code == 503
        assert "Groq completion token configuration" in rejected.value.detail or "between 256 and 32768" in rejected.value.detail


def test_groq_reasoning_effort_is_only_set_for_documented_gpt_oss_model(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": []}

    class FakeClient:
        def __init__(self, timeout):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, _endpoint, *, json, **_kwargs):
            captured.update(json)
            return FakeResponse()

    monkeypatch.setattr(analysis.httpx, "Client", FakeClient)
    analysis._groq_request("synthetic-key", "custom/model", "Skills: Python")
    assert "reasoning_effort" not in captured


def test_groq_success_diagnostics_include_only_safe_aggregate_metrics(monkeypatch, caplog):
    monkeypatch.setenv("SKILLFAIR_LLM_PROVIDER", "groq")
    monkeypatch.setenv("SKILLFAIR_GROQ_API_KEY", "groq-unit-test-secret")
    monkeypatch.setenv("SKILLFAIR_GROQ_MAX_COMPLETION_TOKENS", "3000")
    body = groq_response_body(json.dumps({"skills": [_groq_skill_record(1)]}))
    body["usage"] = {"completion_tokens": 123}
    resume_text = "Python project detail.\n\nSQL project detail."
    monkeypatch.setattr(analysis, "_groq_request", lambda *_args: body)

    with caplog.at_level("INFO", logger="app.analysis"):
        result = analyze_skills(resume_text, requested_mode="ai", consent_to_ai_provider=True)

    expected_prompt_chars = len(analysis.GROQ_SYSTEM_INSTRUCTION) + len(
        "Numbered resume source lines (untrusted):\n[1] Python project detail.\n[2] SQL project detail."
    )
    assert result.skills[0].supporting_excerpt == "Python project detail."
    assert "upstream_http_status=200" in caplog.text
    assert "provider_error_category=none" in caplog.text
    assert "finish_reason=stop" in caplog.text
    assert "max_completion_tokens=3000" in caplog.text
    assert "source_line_count=2" in caplog.text
    assert f"prompt_char_count={expected_prompt_chars}" in caplog.text
    assert "completion_tokens=123" in caplog.text
    assert "Python project detail" not in caplog.text
    assert "groq-unit-test-secret" not in caplog.text


@pytest.mark.parametrize(
    "body",
    [
        groq_response_body(None, refusal="private refusal details"),
        {"choices": []},
        groq_response_body("not json"),
        {"choices": [{"message": {"content": None}, "finish_reason": "length"}]},
    ],
)
def test_groq_refusal_and_malformed_responses_are_sanitized(monkeypatch, body):
    monkeypatch.setenv("SKILLFAIR_LLM_PROVIDER", "groq")
    monkeypatch.setenv("SKILLFAIR_GROQ_API_KEY", "groq-unit-test-secret")
    monkeypatch.setattr(analysis, "_groq_request", lambda *_args: body)

    with pytest.raises(HTTPException) as rejected:
        analyze_skills("private resume text", requested_mode="ai", consent_to_ai_provider=True)

    assert rejected.value.status_code == 502
    assert "private refusal details" not in rejected.value.detail
    assert "private resume text" not in rejected.value.detail
    assert "groq-unit-test-secret" not in rejected.value.detail


def test_groq_parser_accepts_documented_chat_completion_shape_and_ignores_metadata():
    content = '{"skills":[]}'
    body = {
        "id": "chatcmpl-example",
        "object": "chat.completion",
        "model": "openai/gpt-oss-20b",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content, "reasoning": "not part of the payload", "refusal": None},
            "finish_reason": "stop",
        }],
        "usage": {"completion_tokens": 1},
        "x_groq": {"id": "request-metadata"},
    }

    assert analysis._groq_response_text(body) == content


@pytest.mark.parametrize(
    ("body", "finish_reason", "content_present", "failure_category"),
    [
        ({"choices": []}, "missing", False, "response_shape"),
        (groq_response_body("private resume with contact dev@example.test", refusal="private refusal text"), "stop", True, "refusal"),
        ({"choices": [{"message": {"content": '{"skills":[]}'}, "finish_reason": "length"}]}, "length", True, "truncated_output"),
        ({"choices": [{"message": {"content": '{"skills":[]}'}, "finish_reason": "tool_calls"}]}, "tool_calls", True, "unexpected_finish_reason"),
        ({"choices": [{"message": {"content": None}, "finish_reason": "stop"}]}, "stop", False, "missing_content"),
        ({"choices": [{"message": {"content": "   "}, "finish_reason": "stop"}]}, "stop", False, "empty_content"),
    ],
)
def test_groq_shape_diagnostics_are_specific_and_sanitized(
    monkeypatch, caplog, body, finish_reason, content_present, failure_category
):
    monkeypatch.setenv("SKILLFAIR_LLM_PROVIDER", "groq")
    monkeypatch.setenv("SKILLFAIR_GROQ_API_KEY", "groq-unit-test-secret")
    monkeypatch.setattr(analysis, "_groq_request", lambda *_args: body)

    with caplog.at_level("WARNING", logger="app.analysis"), pytest.raises(HTTPException) as rejected:
        analyze_skills("private resume with contact dev@example.test", requested_mode="ai", consent_to_ai_provider=True)

    assert rejected.value.status_code == 502
    assert "upstream_http_status=200" in caplog.text
    assert "provider_error_category=none" in caplog.text
    assert f"finish_reason={finish_reason}" in caplog.text
    assert f"content_present={content_present}" in caplog.text
    assert f"validation_failure_category={failure_category}" in caplog.text
    assert "groq-unit-test-secret" not in caplog.text
    assert "dev@example.test" not in caplog.text
    assert "private refusal text" not in caplog.text
    assert "private resume with contact" not in caplog.text


@pytest.mark.parametrize(
    ("skills", "validation_failure_category"),
    [
        ([{"skill_name": "Python"}], "missing_evidence_line_id"),
        ([{
            "skill_name": "Python",
            "evidence_type": "explicit",
            "supporting_excerpt": "Candidate email: dev@example.test",
            "relevance_explanation": "A direct mention.",
            "validation_status": "evidence_found",
        }], "model_supplied_excerpt_rejected"),
        ([{
            "skill_name": "developer@example.test",
            "evidence_type": "explicit",
            "evidence_line_id": 1,
            "relevance_explanation": "A direct mention.",
            "validation_status": "evidence_found",
        }], "pii_skill_name_validation"),
    ],
)
def test_groq_skill_validation_diagnostics_do_not_log_model_content(
    monkeypatch, caplog, skills, validation_failure_category
):
    monkeypatch.setenv("SKILLFAIR_LLM_PROVIDER", "groq")
    monkeypatch.setenv("SKILLFAIR_GROQ_API_KEY", "groq-unit-test-secret")
    body = groq_response_body(json.dumps({"skills": skills}))
    monkeypatch.setattr(analysis, "_groq_request", lambda *_args: body)
    resume_text = "Python resume text without personal contact data"

    with caplog.at_level("WARNING", logger="app.analysis"), pytest.raises(HTTPException) as rejected:
        analyze_skills(resume_text, requested_mode="ai", consent_to_ai_provider=True)

    assert rejected.value.status_code == 502
    assert "finish_reason=stop" in caplog.text
    assert "content_present=True" in caplog.text
    assert f"validation_failure_category={validation_failure_category}" in caplog.text
    assert "groq-unit-test-secret" not in caplog.text
    assert "dev@example.test" not in caplog.text
    assert resume_text not in caplog.text
    assert "Candidate email" not in caplog.text


def _groq_skill_record(line_id, **extra):
    return {
        "skill_name": "Python",
        "evidence_type": "explicit",
        "evidence_line_id": line_id,
        "relevance_explanation": "Python is named in the supplied resume line.",
        "validation_status": "evidence_found",
        **extra,
    }


def test_groq_line_references_resolve_exact_source_lines_and_pass_existing_validation(monkeypatch):
    monkeypatch.setenv("SKILLFAIR_LLM_PROVIDER", "groq")
    monkeypatch.setenv("SKILLFAIR_GROQ_API_KEY", "groq-unit-test-secret")
    source = "  Built Python,\tdata pipelines.  \n\nSQL reporting."
    body = groq_response_body(json.dumps({"skills": [
        _groq_skill_record(2),
        {**_groq_skill_record(1), "skill_name": "Python"},
    ]}))
    monkeypatch.setattr(analysis, "_groq_request", lambda *_args: body)

    result = analyze_skills(source, requested_mode="ai", consent_to_ai_provider=True)

    assert [skill.supporting_excerpt for skill in result.skills] == ["SQL reporting.", "  Built Python,\tdata pipelines.  "]
    assert all(skill.supporting_excerpt in source for skill in result.skills)


@pytest.mark.parametrize(
    ("line_id", "extra", "expected_category"),
    [
        (999, {}, "unknown_evidence_line_id"),
        (None, {}, "missing_evidence_line_id"),
        ([1, 2], {}, "invalid_evidence_line_id"),
        (True, {}, "invalid_evidence_line_id"),
        (1, {"supporting_excerpt": "invented quote"}, "model_supplied_excerpt_rejected"),
    ],
)
def test_groq_rejects_invalid_or_injected_evidence_references_safely(
    monkeypatch, caplog, line_id, extra, expected_category
):
    monkeypatch.setenv("SKILLFAIR_LLM_PROVIDER", "groq")
    monkeypatch.setenv("SKILLFAIR_GROQ_API_KEY", "groq-unit-test-secret")
    record = _groq_skill_record(line_id, **extra) if line_id is not None else _groq_skill_record(None, **extra)
    if line_id is None:
        record.pop("evidence_line_id")
    body = groq_response_body(json.dumps({"skills": [record]}))
    monkeypatch.setattr(analysis, "_groq_request", lambda *_args: body)
    resume_text = "Python is named here."

    with caplog.at_level("WARNING", logger="app.analysis"), pytest.raises(HTTPException) as rejected:
        analyze_skills(resume_text, requested_mode="ai", consent_to_ai_provider=True)

    assert rejected.value.status_code == 502
    assert f"validation_failure_category={expected_category}" in caplog.text
    assert "invented quote" not in caplog.text
    assert resume_text not in caplog.text
    assert "groq-unit-test-secret" not in caplog.text


def test_groq_rejects_more_than_thirty_skills_without_truncating(monkeypatch, caplog):
    monkeypatch.setenv("SKILLFAIR_LLM_PROVIDER", "groq")
    monkeypatch.setenv("SKILLFAIR_GROQ_API_KEY", "groq-unit-test-secret")
    records = [{**_groq_skill_record(1), "skill_name": f"Skill {index}"} for index in range(31)]
    monkeypatch.setattr(analysis, "_groq_request", lambda *_args: groq_response_body(json.dumps({"skills": records})))

    with caplog.at_level("WARNING", logger="app.analysis"), pytest.raises(HTTPException) as rejected:
        analyze_skills("Python appears in this source line.", requested_mode="ai", consent_to_ai_provider=True)

    assert rejected.value.status_code == 502
    assert "validation_failure_category=application_skill_limit" in caplog.text
    assert "max_completion_tokens=8192" in caplog.text

    with pytest.raises(ValueError, match="skills list exceeds the response limit"):
        validate_skills(
            [{
                "skill_name": f"Skill {index}",
                "evidence_type": "explicit",
                "supporting_excerpt": "Python",
                "relevance_explanation": "Named in source text.",
                "validation_status": "evidence_found",
            } for index in range(31)],
            "Python",
            max_skills=30,
        )


def test_groq_invalid_json_is_diagnosed_without_logging_content(monkeypatch, caplog):
    monkeypatch.setenv("SKILLFAIR_LLM_PROVIDER", "groq")
    monkeypatch.setenv("SKILLFAIR_GROQ_API_KEY", "groq-unit-test-secret")
    sensitive_invalid_json = '{"skills": [], "contact": "dev@example.test", broken'
    monkeypatch.setattr(analysis, "_groq_request", lambda *_args: groq_response_body(sensitive_invalid_json))

    with caplog.at_level("WARNING", logger="app.analysis"), pytest.raises(HTTPException):
        analyze_skills("private resume", requested_mode="ai", consent_to_ai_provider=True)

    assert "upstream_http_status=200" in caplog.text
    assert "finish_reason=stop" in caplog.text
    assert "content_present=True" in caplog.text
    assert "validation_failure_category=invalid_json" in caplog.text
    assert "dev@example.test" not in caplog.text
    assert "private resume" not in caplog.text
    assert "groq-unit-test-secret" not in caplog.text
    assert sensitive_invalid_json not in caplog.text


@pytest.mark.parametrize(
    ("status_code", "expected_text"),
    [
        (400, "rejected the request"),
        (401, "authentication"),
        (403, "permission"),
        (404, "not found"),
        (429, "quota"),
        (503, "temporarily unavailable"),
    ],
)
def test_groq_auth_and_rate_limit_errors_are_safe(monkeypatch, caplog, status_code, expected_text):
    monkeypatch.setenv("SKILLFAIR_LLM_PROVIDER", "groq")
    monkeypatch.setenv("SKILLFAIR_GROQ_API_KEY", "groq-unit-test-secret")
    request = analysis.httpx.Request("POST", analysis.GROQ_CHAT_COMPLETIONS_URL)
    response = analysis.httpx.Response(status_code, json={"error": {"message": "private resume and groq-unit-test-secret"}}, request=request)
    failure = analysis.httpx.HTTPStatusError("private provider response", request=request, response=response)
    monkeypatch.setattr(analysis, "_groq_request", lambda *_args: (_ for _ in ()).throw(failure))

    with caplog.at_level("WARNING", logger="app.analysis"), pytest.raises(HTTPException) as rejected:
        analyze_skills("private resume", requested_mode="ai", consent_to_ai_provider=True)

    assert rejected.value.status_code == 502
    assert expected_text in rejected.value.detail.lower()
    assert "groq-unit-test-secret" not in rejected.value.detail
    assert "private resume" not in rejected.value.detail
    assert "private provider response" not in caplog.text
    assert "groq-unit-test-secret" not in caplog.text
    assert f"upstream_http_status={status_code}" in caplog.text
    assert "finish_reason=not_available" in caplog.text
    assert "content_present=False" in caplog.text
    assert "validation_failure_category=not_run" in caplog.text
    assert "max_completion_tokens=8192" in caplog.text
    assert "source_line_count=1" in caplog.text
    assert "prompt_char_count=" in caplog.text


@pytest.mark.parametrize(
    ("status_code", "error_code", "expected_category"),
    [
        (429, "rate_limit_exceeded", "rate_limit"),
        (429, "insufficient_quota", "quota"),
        (413, None, "request_too_large"),
        (400, "unsupported_schema", "unsupported_schema"),
        (404, "model_not_found", "model_unavailable"),
        (422, "unrecognized-private-provider-code", "provider_validation_error"),
        (503, None, "provider_server_error"),
        (503, "model_not_found", "provider_server_error"),
    ],
)
def test_groq_provider_error_classification_is_allowlisted_and_sanitized(
    monkeypatch, caplog, status_code, error_code, expected_category
):
    monkeypatch.setenv("SKILLFAIR_LLM_PROVIDER", "groq")
    monkeypatch.setenv("SKILLFAIR_GROQ_API_KEY", "groq-unit-test-secret")
    error = {"message": "private upstream detail with resume PII sam@example.test"}
    if error_code is not None:
        error["code"] = error_code
    request = analysis.httpx.Request("POST", analysis.GROQ_CHAT_COMPLETIONS_URL)
    response = analysis.httpx.Response(status_code, json={"error": error}, request=request)
    failure = analysis.httpx.HTTPStatusError("private response", request=request, response=response)
    monkeypatch.setattr(analysis, "_groq_request", lambda *_args: (_ for _ in ()).throw(failure))

    with caplog.at_level("WARNING", logger="app.analysis"), pytest.raises(HTTPException):
        analyze_skills("private resume text", requested_mode="ai", consent_to_ai_provider=True)

    assert f"upstream_http_status={status_code}" in caplog.text
    assert f"provider_error_category={expected_category}" in caplog.text
    assert "unrecognized-private-provider-code" not in caplog.text
    assert "private upstream detail" not in caplog.text
    assert "sam@example.test" not in caplog.text
    assert "groq-unit-test-secret" not in caplog.text


@pytest.mark.parametrize(
    ("failure", "status", "message"),
    [
        (analysis.httpx.ConnectTimeout("secret resume"), 504, "connection timed out"),
        (analysis.httpx.ReadTimeout("secret resume"), 504, "did not return a response"),
        (analysis.httpx.ConnectError("secret resume"), 502, "could not connect"),
    ],
)
def test_groq_timeout_and_network_errors_are_sanitized(monkeypatch, failure, status, message):
    monkeypatch.setenv("SKILLFAIR_LLM_PROVIDER", "groq")
    monkeypatch.setenv("SKILLFAIR_GROQ_API_KEY", "groq-unit-test-secret")
    monkeypatch.setattr(analysis, "_groq_request", lambda *_args: (_ for _ in ()).throw(failure))

    with pytest.raises(HTTPException) as rejected:
        analyze_skills("secret resume", requested_mode="ai", consent_to_ai_provider=True)

    assert rejected.value.status_code == status
    assert message in rejected.value.detail.lower()
    assert "secret resume" not in rejected.value.detail
    assert "groq-unit-test-secret" not in rejected.value.detail


def test_groq_requires_per_request_consent_before_configuration_or_call(monkeypatch):
    monkeypatch.setenv("SKILLFAIR_LLM_PROVIDER", "groq")
    monkeypatch.setattr(analysis, "groq_settings", lambda: (_ for _ in ()).throw(AssertionError("settings must not be read before consent")))
    monkeypatch.setattr(analysis, "_groq_request", lambda *_args: (_ for _ in ()).throw(AssertionError("provider call must not run")))

    with pytest.raises(HTTPException) as rejected:
        analyze_skills("private resume", requested_mode="ai", consent_to_ai_provider=False)

    assert rejected.value.status_code == 400
    assert "consent" in rejected.value.detail.lower()
    assert "private resume" not in rejected.value.detail


def test_groq_consent_is_not_reused_between_analysis_requests(monkeypatch):
    monkeypatch.setenv("SKILLFAIR_LLM_PROVIDER", "groq")
    monkeypatch.setenv("SKILLFAIR_GROQ_API_KEY", "groq-unit-test-secret")
    valid = groq_response_body('{"skills":[]}')
    calls = []
    monkeypatch.setattr(analysis, "_groq_request", lambda *_args: (calls.append(True), valid)[1])

    completed = analyze_skills("synthetic", requested_mode="ai", consent_to_ai_provider=True)
    assert completed.analysis_mode == "ai"
    with pytest.raises(HTTPException) as second_request:
        analyze_skills("synthetic", requested_mode="ai", consent_to_ai_provider=False)

    assert second_request.value.status_code == 400
    assert calls == [True]


def test_groq_endpoint_requires_generic_provider_consent_before_extraction(monkeypatch):
    monkeypatch.setenv("SKILLFAIR_LLM_PROVIDER", "groq")
    upload = UploadFile(file=BytesIO(docx_bytes("Skills: Python")), filename="resume.docx")
    monkeypatch.setattr(main, "validate_and_extract", lambda *_args: (_ for _ in ()).throw(AssertionError("text extraction must wait for consent")))

    with pytest.raises(HTTPException) as rejected:
        discover_skills(upload, analysis_mode="ai", consent_to_gemini="true")

    assert rejected.value.status_code == 400
    assert "Groq" in rejected.value.detail
    assert upload.file.closed


def test_groq_endpoint_accepts_generic_consent_and_stores_validated_skills(monkeypatch):
    monkeypatch.setenv("SKILLFAIR_LLM_PROVIDER", "groq")
    monkeypatch.setenv("SKILLFAIR_GROQ_API_KEY", "groq-unit-test-secret")
    store = AnalysisStore()
    monkeypatch.setattr(main, "analysis_store", store)
    requested = []
    skill = SkillEvidence.model_validate({
        "skill_name": "Python",
        "evidence_type": "explicit",
        "supporting_excerpt": "Skills: Python",
        "relevance_explanation": "Named directly.",
        "validation_status": "evidence_found",
    })

    def fake_analyze(text, requested_mode, legacy_consent, *, consent_to_ai_provider=None):
        requested.append((requested_mode, legacy_consent, consent_to_ai_provider))
        return SkillsDiscoverResponse(analysis_mode="ai", message="AI analysis complete.", extracted_characters=len(text), skills=[skill])

    monkeypatch.setattr(main, "analyze_skills", fake_analyze)
    upload = UploadFile(file=BytesIO(docx_bytes("Skills: Python")), filename="resume.docx")
    response = discover_skills(upload, analysis_mode="ai", consent_to_ai_provider="true", consented_ai_provider="groq")

    assert requested == [("ai", False, True)]
    assert response.analysis_id
    assert store.resolve(response.analysis_id) == [skill]


def test_groq_analyzed_evidence_flows_through_existing_provenance(monkeypatch):
    monkeypatch.setenv("SKILLFAIR_LLM_PROVIDER", "groq")
    monkeypatch.setenv("SKILLFAIR_GROQ_API_KEY", "groq-unit-test-secret")
    store = AnalysisStore()
    monkeypatch.setattr(main, "analysis_store", store)
    model_body = groq_response_body(json.dumps({"skills": [{
        "skill_name": "Python",
        "evidence_type": "explicit",
        "evidence_line_id": 1,
        "relevance_explanation": "Python is named in the skills section.",
        "validation_status": "evidence_found",
    }]}))
    monkeypatch.setattr(analysis, "_groq_request", lambda *_args: model_body)
    upload = UploadFile(file=BytesIO(docx_bytes("Skills: Python")), filename="resume.docx")

    response = discover_skills(upload, analysis_mode="ai", consent_to_ai_provider="true", consented_ai_provider="groq")

    persisted = store.resolve(response.analysis_id)
    assert response.analysis_mode == "ai"
    assert persisted is not None
    assert persisted[0].supporting_excerpt == "Skills: Python"
    assert persisted[0].detection_method == "ai"


def test_groq_request_rejects_consent_for_a_different_configured_provider_before_extraction(monkeypatch):
    monkeypatch.setenv("SKILLFAIR_LLM_PROVIDER", "groq")
    upload = UploadFile(file=BytesIO(docx_bytes("Skills: Python")), filename="resume.docx")
    monkeypatch.setattr(main, "validate_and_extract", lambda *_args: (_ for _ in ()).throw(AssertionError("provider mismatch must be rejected before extraction")))

    with pytest.raises(HTTPException) as rejected:
        discover_skills(
            upload,
            analysis_mode="ai",
            consent_to_ai_provider="true",
            consented_ai_provider="gemini",
        )

    assert rejected.value.status_code == 409
    assert "provider changed" in rejected.value.detail.lower()
    assert upload.file.closed


def test_local_analysis_never_checks_or_calls_groq(monkeypatch):
    monkeypatch.setenv("SKILLFAIR_LLM_PROVIDER", "groq")
    monkeypatch.setattr(analysis, "groq_settings", lambda: (_ for _ in ()).throw(AssertionError("local mode must not read provider configuration")))
    monkeypatch.setattr(analysis, "_groq_request", lambda *_args: (_ for _ in ()).throw(AssertionError("local mode must not call Groq")))

    result = analyze_skills("Skills\nPython", requested_mode="local")
    assert result.analysis_mode == "local"
    assert [skill.skill_name for skill in result.skills] == ["Python"]


def test_ai_mode_without_groq_configuration_does_not_fall_back(monkeypatch):
    monkeypatch.setenv("SKILLFAIR_LLM_PROVIDER", "groq")
    monkeypatch.delenv("SKILLFAIR_GROQ_API_KEY", raising=False)
    monkeypatch.setattr(analysis, "_groq_request", lambda *_args: (_ for _ in ()).throw(AssertionError("no request should be sent")))

    with pytest.raises(HTTPException) as unavailable:
        analyze_skills("Skills\nPython", requested_mode="ai", consent_to_ai_provider=True)

    assert unavailable.value.status_code == 503
    assert "SKILLFAIR_GROQ_API_KEY" in unavailable.value.detail


def test_public_provider_status_does_not_expose_groq_credentials(monkeypatch):
    monkeypatch.setenv("SKILLFAIR_LLM_PROVIDER", "groq")
    monkeypatch.setenv("SKILLFAIR_GROQ_API_KEY", "groq-unit-test-secret")
    status = main.skills_provider_status().model_dump()

    assert status["provider"] == "groq"
    assert status["configured"] is True
    assert "groq-unit-test-secret" not in json.dumps(status)
    assert "api_key" not in status
