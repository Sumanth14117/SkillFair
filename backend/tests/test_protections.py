import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.protections import (
    InMemoryRateLimiter,
    RateLimitMiddleware,
    RequestBodyLimitMiddleware,
    _request_group,
    configured_body_limit,
)
from app.main import app as skillfair_app


def _run(coroutine):
    """Drive immediate in-memory ASGI fixtures without event-loop sockets."""
    try:
        coroutine.send(None)
    except StopIteration as completed:
        return completed.value
    raise AssertionError("test ASGI fixture unexpectedly suspended on external I/O")


def _scope(path="/api/skills/discover", method="POST", headers=(), client=("198.51.100.1", 1234)):
    return {
        "type": "http", "method": method, "path": path,
        "headers": list(headers), "client": client, "http_version": "1.1",
    }


def _response_status(messages):
    return next(message["status"] for message in messages if message["type"] == "http.response.start")


def test_body_limit_allows_requests_below_configured_limit(monkeypatch):
    monkeypatch.setenv("SKILLFAIR_MAX_REQUEST_BODY_BYTES", "8")
    received = 0
    sent = []
    async def receive():
        nonlocal received
        received += 1
        return {"type": "http.request", "body": b"1234567", "more_body": False}
    async def send(message):
        sent.append(message)
    async def app(_scope, downstream_receive, downstream_send):
        message = await downstream_receive()
        assert len(message["body"]) == 7
        await downstream_send({"type": "http.response.start", "status": 200, "headers": []})
        await downstream_send({"type": "http.response.body", "body": b"ok", "more_body": False})

    _run(RequestBodyLimitMiddleware(app, limit=8)(_scope(headers=[(b"content-length", b"7")]), receive, send))
    assert _response_status(sent) == 200
    assert received == 1


def test_oversized_content_length_is_rejected_before_downstream_multipart_processing(monkeypatch):
    monkeypatch.setenv("SKILLFAIR_MAX_REQUEST_BODY_BYTES", "8")
    downstream_called = False
    receive_calls = 0
    sent = []
    async def receive():
        nonlocal receive_calls
        receive_calls += 1
        return {"type": "http.request", "body": b"should not be read", "more_body": False}
    async def send(message):
        sent.append(message)
    async def multipart_parser(_scope, _receive, _send):
        nonlocal downstream_called
        downstream_called = True

    _run(RequestBodyLimitMiddleware(multipart_parser)(_scope(headers=[(b"content-length", b"9"), (b"content-type", b"multipart/form-data; boundary=x")]), receive, send))
    assert _response_status(sent) == 413
    assert not downstream_called
    assert receive_calls == 0
    assert b"configured size limit" in sent[1]["body"]


def test_streamed_body_without_content_length_stops_at_first_over_limit_chunk():
    chunks = [b"1234", b"5678", b"this trailing payload must not be read"]
    read_count = 0
    endpoint_called = False
    sent = []
    async def receive():
        nonlocal read_count
        chunk = chunks[read_count]
        read_count += 1
        return {"type": "http.request", "body": chunk, "more_body": read_count < len(chunks)}
    async def send(message):
        sent.append(message)
    async def multipart_parser(_scope, downstream_receive, _send):
        nonlocal endpoint_called
        while True:
            message = await downstream_receive()
            if not message.get("more_body"):
                endpoint_called = True
                return

    _run(RequestBodyLimitMiddleware(multipart_parser, limit=5)(_scope(), receive, send))
    assert _response_status(sent) == 413
    assert read_count == 2
    assert not endpoint_called


def test_consuming_route_that_handles_body_limit_error_still_cannot_send_success():
    chunks = [b"1234", b"5678"]
    read_count = 0
    sent = []
    async def receive():
        nonlocal read_count
        chunk = chunks[read_count]
        read_count += 1
        return {"type": "http.request", "body": chunk, "more_body": read_count < len(chunks)}
    async def send(message):
        sent.append(message)
    async def error_handling_route(_scope, downstream_receive, downstream_send):
        try:
            while True:
                message = await downstream_receive()
                if not message.get("more_body"):
                    break
        except Exception:
            # Models an inner server error handler attempting to send a 500.
            await downstream_send({"type": "http.response.start", "status": 500, "headers": []})
            await downstream_send({"type": "http.response.body", "body": b"internal", "more_body": False})

    _run(RequestBodyLimitMiddleware(error_handling_route, limit=5)(_scope(headers=[]), receive, send))
    assert _response_status(sent) == 413
    assert b"internal" not in sent[-1]["body"]


def test_chunked_body_without_content_length_below_limit_is_counted_for_consuming_route():
    chunks = [b"123", b"45"]
    read_count = 0
    sent = []
    async def receive():
        nonlocal read_count
        chunk = chunks[read_count]
        read_count += 1
        return {"type": "http.request", "body": chunk, "more_body": read_count < len(chunks)}
    async def send(message):
        sent.append(message)
    async def consuming_app(_scope, downstream_receive, downstream_send):
        body = bytearray()
        while True:
            message = await downstream_receive()
            body.extend(message.get("body", b""))
            if not message.get("more_body"):
                break
        assert bytes(body) == b"12345"
        await downstream_send({"type": "http.response.start", "status": 200, "headers": []})
        await downstream_send({"type": "http.response.body", "body": b"ok", "more_body": False})

    _run(RequestBodyLimitMiddleware(consuming_app, limit=5)(_scope(headers=[]), receive, send))
    assert _response_status(sent) == 200
    assert read_count == 2


@pytest.mark.parametrize(
    ("chunks", "limit", "expected_status", "expected_reads"),
    [([b"123", b"45"], 5, 200, 2), ([b"123", b"456", b"never read"], 5, 413, 2)],
)
def test_unconsumed_chunked_body_is_drained_and_limited_before_response(chunks, limit, expected_status, expected_reads):
    read_count = 0
    sent = []
    async def receive():
        nonlocal read_count
        chunk = chunks[read_count]
        read_count += 1
        return {"type": "http.request", "body": chunk, "more_body": read_count < len(chunks)}
    async def send(message):
        sent.append(message)
    async def body_ignoring_route(_scope, _receive, downstream_send):
        await downstream_send({"type": "http.response.start", "status": 200, "headers": []})
        await downstream_send({"type": "http.response.body", "body": b"ok", "more_body": False})

    _run(RequestBodyLimitMiddleware(body_ignoring_route, limit=limit)(
        _scope(method="GET", headers=[(b"transfer-encoding", b"chunked")]), receive, send
    ))
    assert _response_status(sent) == expected_status
    assert read_count == expected_reads
    if expected_status == 413:
        assert b"configured size limit" in sent[-1]["body"]


def test_bodyless_http11_get_does_not_wait_for_request_body():
    read_count = 0
    sent = []
    async def receive():
        nonlocal read_count
        read_count += 1
        return {"type": "http.request", "body": b"", "more_body": False}
    async def send(message):
        sent.append(message)
    async def health_route(_scope, _receive, downstream_send):
        await downstream_send({"type": "http.response.start", "status": 200, "headers": []})
        await downstream_send({"type": "http.response.body", "body": b"ok", "more_body": False})

    _run(RequestBodyLimitMiddleware(health_route, limit=5)(
        _scope(path="/api/health", method="GET", headers=[]), receive, send
    ))
    assert _response_status(sent) == 200
    assert read_count == 0


def test_declared_get_body_within_limit_is_still_drained():
    receive_calls = 0
    sent = []
    async def receive():
        nonlocal receive_calls
        receive_calls += 1
        if receive_calls == 1:
            return {"type": "http.request", "body": b"123", "more_body": True}
        return {"type": "http.request", "body": b"45", "more_body": False}
    async def send(message):
        sent.append(message)
    async def ignored_body_route(_scope, _receive, downstream_send):
        await downstream_send({"type": "http.response.start", "status": 200, "headers": []})
        await downstream_send({"type": "http.response.body", "body": b"ok", "more_body": False})

    _run(RequestBodyLimitMiddleware(ignored_body_route, limit=5)(
        _scope(method="GET", headers=[(b"content-length", b"5")]), receive, send
    ))
    assert _response_status(sent) == 200
    assert receive_calls == 2


def test_client_disconnect_during_unconsumed_body_drain_suppresses_response_without_hanging():
    receive_calls = 0
    sent = []
    async def receive():
        nonlocal receive_calls
        receive_calls += 1
        if receive_calls == 1:
            return {"type": "http.request", "body": b"partial", "more_body": True}
        return {"type": "http.disconnect"}
    async def send(message):
        sent.append(message)
    async def body_ignoring_route(_scope, _receive, downstream_send):
        await downstream_send({"type": "http.response.start", "status": 200, "headers": []})
        await downstream_send({"type": "http.response.body", "body": b"ok", "more_body": False})

    _run(RequestBodyLimitMiddleware(body_ignoring_route, limit=20)(
        _scope(method="GET", headers=[(b"transfer-encoding", b"chunked")]), receive, send
    ))
    assert receive_calls == 2
    assert sent == []


def test_middleware_propagates_cancellation_and_creates_no_body_drain_task():
    sent = []
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}
    async def send(message):
        sent.append(message)
    async def cancelled_app(_scope, _receive, _send):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        _run(RequestBodyLimitMiddleware(cancelled_app, limit=10)(_scope(), receive, send))
    assert sent == []


def test_body_limit_uses_configured_safe_default_and_rejects_invalid_configuration(monkeypatch):
    monkeypatch.delenv("SKILLFAIR_MAX_REQUEST_BODY_BYTES", raising=False)
    assert configured_body_limit() == 12 * 1024 * 1024
    monkeypatch.setenv("SKILLFAIR_MAX_REQUEST_BODY_BYTES", str(60 * 1024 * 1024 + 1))
    try:
        configured_body_limit()
    except ValueError:
        pass
    else:
        raise AssertionError("out-of-range body limit must be rejected")


def test_rate_limiter_allows_within_limit_then_returns_retry_after_and_separates_clients():
    now = [10.0]
    limiter = InMemoryRateLimiter(default_limit=2, discovery_limit=2, orchestrator_limit=2, clock=lambda: now[0])
    assert limiter.check("192.0.2.1", "default").allowed
    assert limiter.check("192.0.2.1", "default").allowed
    rejected = limiter.check("192.0.2.1", "default")
    assert not rejected.allowed
    assert rejected.retry_after == 50
    assert limiter.check("192.0.2.2", "default").allowed


def test_expired_rate_limit_entries_are_removed_and_quota_resets():
    now = [0.0]
    limiter = InMemoryRateLimiter(default_limit=1, window_seconds=10, clock=lambda: now[0])
    assert limiter.check("192.0.2.1", "default").allowed
    assert limiter.tracked_buckets == 1
    now[0] = 10.0
    assert limiter.check("192.0.2.2", "default").allowed
    assert limiter.tracked_buckets == 1
    assert limiter.check("192.0.2.1", "default").allowed


def test_rate_limit_tracker_is_bounded_and_fails_closed_for_new_buckets():
    now = [0.0]
    limiter = InMemoryRateLimiter(default_limit=1, max_entries=1, clock=lambda: now[0])
    assert limiter.check("192.0.2.1", "default").allowed
    full = limiter.check("192.0.2.2", "default")
    assert not full.allowed
    assert full.retry_after == 60
    assert limiter.tracked_buckets == 1
    now[0] = 60.0
    assert limiter.check("192.0.2.2", "default").allowed
    assert limiter.tracked_buckets == 1


def test_concurrent_rate_limit_checks_never_exceed_configured_quota():
    limiter = InMemoryRateLimiter(default_limit=11)
    with ThreadPoolExecutor(max_workers=20) as executor:
        outcomes = list(executor.map(lambda _index: limiter.check("192.0.2.8", "default").allowed, range(80)))
    assert sum(outcomes) == 11


def test_rate_limit_middleware_returns_429_and_retry_after_header():
    limiter = InMemoryRateLimiter(default_limit=1, discovery_limit=1, orchestrator_limit=1, clock=lambda: 0.0)
    middleware = RateLimitMiddleware(None, limiter=limiter)
    downstream_calls = 0
    responses = []
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}
    async def send(message):
        responses.append(message)
    async def app(_scope, _receive, _send):
        nonlocal downstream_calls
        downstream_calls += 1
    middleware.app = app

    scope = _scope(headers=[(b"x-forwarded-for", b"203.0.113.200")])
    _run(middleware(scope, receive, send))
    scope["headers"] = [(b"x-forwarded-for", b"203.0.113.201")]
    _run(middleware(scope, receive, send))
    assert downstream_calls == 1
    assert _response_status(responses) == 429
    response_headers = dict(responses[-2]["headers"])
    assert response_headers[b"retry-after"] == b"60"
    assert b"Too many requests" in responses[-1]["body"]


def test_application_places_body_and_rate_limits_before_multipart_parser():
    middleware_names = [item.cls.__name__ for item in skillfair_app.user_middleware]
    assert middleware_names.index("CORSMiddleware") < middleware_names.index("RateLimitMiddleware")
    assert middleware_names.index("RateLimitMiddleware") < middleware_names.index("RequestBodyLimitMiddleware")


def test_rate_limit_policy_uses_specific_buckets_for_discovery_and_orchestrator():
    limiter = InMemoryRateLimiter(default_limit=10, discovery_limit=1, orchestrator_limit=2, auth_limit=3)
    assert limiter.check("192.0.2.1", "discovery").allowed
    assert not limiter.check("192.0.2.1", "discovery").allowed
    assert limiter.check("192.0.2.1", "orchestrator").allowed
    assert limiter.check("192.0.2.1", "orchestrator").allowed
    assert not limiter.check("192.0.2.1", "orchestrator").allowed
    assert limiter.check("192.0.2.1", "auth").allowed
    assert limiter.check("192.0.2.1", "auth").allowed
    assert limiter.check("192.0.2.1", "auth").allowed
    assert not limiter.check("192.0.2.1", "auth").allowed
    assert _request_group("/api/skills/discover/") == "discovery"
    assert _request_group("/api/career/orchestrator/runs/abc/review") == "orchestrator"
    assert _request_group("/api/auth/login") == "auth"
