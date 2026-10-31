"""Small, process-local ASGI request-size and rate-limit protections."""

import json
import math
import os
import threading
import time
import asyncio
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Awaitable, Callable


DEFAULT_MAX_REQUEST_BODY_BYTES = 12 * 1024 * 1024
HARD_MAX_REQUEST_BODY_BYTES = 60 * 1024 * 1024
RATE_WINDOW_SECONDS = 60
MAX_TRACKED_CLIENT_BUCKETS = 10_000
_MAX_RATE = 10_000


def configured_body_limit() -> int:
    raw = os.getenv("SKILLFAIR_MAX_REQUEST_BODY_BYTES", str(DEFAULT_MAX_REQUEST_BODY_BYTES))
    try:
        limit = int(raw)
    except ValueError as exc:
        raise ValueError("invalid request body limit") from exc
    if not 1 <= limit <= HARD_MAX_REQUEST_BODY_BYTES:
        raise ValueError("request body limit is outside its allowed range")
    return limit


def _configured_rate(name: str, default: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        limit = int(raw)
    except ValueError as exc:
        raise ValueError("invalid rate limit") from exc
    if not 1 <= limit <= _MAX_RATE:
        raise ValueError("rate limit is outside its allowed range")
    return limit


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    retry_after: int = 0


class InMemoryRateLimiter:
    """Thread-safe fixed-window limiter with a hard cap on tracked client buckets."""

    def __init__(
        self,
        default_limit: int = 60,
        discovery_limit: int = 5,
        orchestrator_limit: int = 20,
        auth_limit: int = 10,
        window_seconds: int = RATE_WINDOW_SECONDS,
        max_entries: int = MAX_TRACKED_CLIENT_BUCKETS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if min(default_limit, discovery_limit, orchestrator_limit, auth_limit, window_seconds, max_entries) <= 0:
            raise ValueError("rate limiter settings must be positive")
        self._limits = {
            "default": default_limit,
            "discovery": discovery_limit,
            "orchestrator": orchestrator_limit,
            "auth": auth_limit,
        }
        self._window_seconds = window_seconds
        self._max_entries = max_entries
        self._clock = clock
        self._entries: OrderedDict[tuple[str, str], tuple[int, int]] = OrderedDict()
        self._lock = threading.Lock()

    def check(self, client: str, group: str) -> RateLimitDecision:
        now = self._clock()
        window_start = int(now // self._window_seconds) * self._window_seconds
        limit = self._limits.get(group, self._limits["default"])
        key = (client, group)
        with self._lock:
            expired = [
                existing for existing, (start, _count) in self._entries.items()
                if start + self._window_seconds <= now
            ]
            for existing in expired:
                self._entries.pop(existing, None)

            entry = self._entries.get(key)
            if entry is None:
                if len(self._entries) >= self._max_entries:
                    # Fail closed for new clients instead of evicting active buckets
                    # and allowing a client to reset its quota by churning identities.
                    return RateLimitDecision(False, self._window_seconds)
                self._entries[key] = (window_start, 1)
                return RateLimitDecision(True)

            start, count = entry
            self._entries.move_to_end(key)
            if start != window_start:
                self._entries[key] = (window_start, 1)
                return RateLimitDecision(True)
            if count >= limit:
                return RateLimitDecision(False, max(1, math.ceil(start + self._window_seconds - now)))
            self._entries[key] = (start, count + 1)
            return RateLimitDecision(True)

    @property
    def tracked_buckets(self) -> int:
        with self._lock:
            return len(self._entries)


def _request_group(path: str) -> str:
    path = path.rstrip("/") or "/"
    if path == "/api/skills/discover":
        return "discovery"
    if path.startswith("/api/career/orchestrator/runs"):
        return "orchestrator"
    if path.startswith("/api/auth/"):
        return "auth"
    return "default"


def _client_address(scope: dict[str, Any]) -> str:
    # Deliberately ignore X-Forwarded-For and similar headers. ASGI server peer only.
    peer = scope.get("client")
    if isinstance(peer, (tuple, list)) and peer and isinstance(peer[0], str):
        return peer[0][:64]
    return "unknown"


async def _send_json(send: Callable[[dict[str, Any]], Awaitable[None]], status: int, payload: dict[str, Any], headers: list[tuple[bytes, bytes]] | None = None) -> None:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    response_headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode("ascii"))]
    if headers:
        response_headers.extend(headers)
    await send({"type": "http.response.start", "status": status, "headers": response_headers})
    await send({"type": "http.response.body", "body": body, "more_body": False})


class RateLimitMiddleware:
    def __init__(self, app: Any, limiter: InMemoryRateLimiter | None = None) -> None:
        self.app = app
        self.limiter = limiter or InMemoryRateLimiter(
            default_limit=_configured_rate("SKILLFAIR_RATE_LIMIT_PER_MINUTE", 60),
            discovery_limit=_configured_rate("SKILLFAIR_DISCOVERY_RATE_LIMIT_PER_MINUTE", 5),
            orchestrator_limit=_configured_rate("SKILLFAIR_ORCHESTRATOR_RATE_LIMIT_PER_MINUTE", 20),
            auth_limit=_configured_rate("SKILLFAIR_AUTH_RATE_LIMIT_PER_MINUTE", 10),
        )

    async def __call__(self, scope: dict[str, Any], receive: Callable[..., Any], send: Callable[..., Any]) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        method = scope.get("method", "GET").upper()
        if not path.startswith("/api/") or path == "/api/health" or method == "OPTIONS":
            await self.app(scope, receive, send)
            return
        group = _request_group(path)
        decision = self.limiter.check(_client_address(scope), group)
        if not decision.allowed:
            await _send_json(
                send,
                429,
                {"detail": "Too many requests. Please wait before trying again."},
                [(b"retry-after", str(decision.retry_after).encode("ascii"))],
            )
            return
        await self.app(scope, receive, send)


class _RequestBodyLimitExceeded(Exception):
    pass


class RequestBodyLimitMiddleware:
    def __init__(self, app: Any, limit: int | None = None) -> None:
        self.app = app
        self.limit = limit

    async def __call__(self, scope: dict[str, Any], receive: Callable[..., Any], send: Callable[..., Any]) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        try:
            limit = self.limit if self.limit is not None else configured_body_limit()
        except ValueError:
            await _send_json(send, 500, {"detail": "Request-size protection is misconfigured."})
            return

        headers = scope.get("headers", [])
        content_lengths = [value for key, value in headers if key.lower() == b"content-length"]
        transfer_encodings = [value for key, value in headers if key.lower() == b"transfer-encoding"]
        declared_length: int | None = None
        if content_lengths:
            try:
                declared_length = int(content_lengths[0])
            except (ValueError, TypeError):
                declared_length = None
            if declared_length is not None and declared_length > limit:
                await _send_json(send, 413, {"detail": "Request body exceeds the configured size limit."})
                return

        received = 0
        # HTTP/1.x framing defines a request with neither Content-Length nor
        # Transfer-Encoding as bodyless. Do not wait for another ASGI receive
        # event when a body-ignoring route responds to that request: Uvicorn
        # may keep receive() pending after the request has already ended.
        # Requests with explicit or streamed framing still go through the
        # normal byte-counting/draining path below.
        method = scope.get("method", "GET").upper()
        protocol = scope.get("http_version", "1.1")
        bodyless_http1_request = (
            protocol in {"1.0", "1.1"}
            and method in {"GET", "HEAD", "OPTIONS"}
            and not transfer_encodings
            and (not content_lengths or declared_length == 0)
        )
        body_complete = bodyless_http1_request
        disconnected = False
        response_rejected = False
        body_error: str | None = None
        receive_lock = asyncio.Lock()

        async def limited_receive() -> dict[str, Any]:
            nonlocal received, body_complete, disconnected, body_error
            async with receive_lock:
                message = await receive()
                message_type = message.get("type")
                if message_type == "http.request":
                    received += len(message.get("body", b""))
                    if received > limit:
                        body_error = "too_large"
                        raise _RequestBodyLimitExceeded
                    if not message.get("more_body", False):
                        body_complete = True
                elif message_type == "http.disconnect":
                    disconnected = True
                    body_complete = True
                return message

        async def limited_send(message: dict[str, Any]) -> None:
            nonlocal response_rejected
            if response_rejected:
                return
            # Routes that ignore request bodies never call receive(). Drain and
            # count any remaining stream before committing response headers, so
            # an oversized body cannot receive an ordinary success response.
            if message.get("type") == "http.response.start" and not body_complete:
                if body_error is not None:
                    response_rejected = True
                    if body_error == "too_large":
                        await _send_json(send, 413, {"detail": "Request body exceeds the configured size limit."})
                    return
                while not body_complete:
                    try:
                        await limited_receive()
                    except _RequestBodyLimitExceeded:
                        response_rejected = True
                        await _send_json(send, 413, {"detail": "Request body exceeds the configured size limit."})
                        return
            if not disconnected:
                await send(message)

        try:
            await self.app(scope, limited_receive, limited_send)
        except _RequestBodyLimitExceeded:
            # This middleware sits outside Starlette's multipart parser. The parser
            # unwinds and closes partial spooled files before this response is sent.
            if not disconnected:
                response_rejected = True
                await _send_json(send, 413, {"detail": "Request body exceeds the configured size limit."})
