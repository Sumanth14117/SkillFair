from fastapi.testclient import TestClient

from app.main import app


def test_http_api_responses_receive_security_headers_without_hsts():
    with TestClient(app, base_url="http://testserver") as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert response.headers["permissions-policy"] == "camera=(), microphone=(), geolocation=()"
    assert response.headers["content-security-policy"] == "default-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    assert response.headers["cache-control"] == "no-store"
    assert "strict-transport-security" not in response.headers


def test_unauthenticated_account_api_response_is_not_cached():
    with TestClient(app) as client:
        response = client.get("/api/auth/me")
    assert response.status_code == 401
    assert response.headers["cache-control"] == "no-store"


def test_https_responses_receive_hsts_but_forwarded_proto_is_not_trusted():
    with TestClient(app, base_url="https://testserver") as client:
        https_response = client.get("/api/health")
    with TestClient(app, base_url="http://testserver") as client:
        plain_response = client.get("/api/health", headers={"X-Forwarded-Proto": "https"})
    assert https_response.headers["strict-transport-security"] == "max-age=15552000"
    assert "strict-transport-security" not in plain_response.headers


def test_swagger_document_is_not_given_an_api_only_content_security_policy():
    with TestClient(app) as client:
        response = client.get("/docs")
    assert response.status_code == 200
    assert "content-security-policy" not in response.headers
    assert response.headers["x-frame-options"] == "DENY"
