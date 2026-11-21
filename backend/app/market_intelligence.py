"""Validated market-data boundary with bounded process-local freshness caching.

Provider implementations must use fixed, reviewed endpoints, bounded network
timeouts and response sizes, and return source-linked calculations. No external
provider is enabled until its access and data-use terms have been approved.
"""

from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
import threading
import time
from typing import Protocol

from pydantic import ValidationError

from .models import (
    MarketDataSnapshot,
    MarketIntelligenceRequest,
    MarketIntelligenceResponse,
    MarketProviderStatusResponse,
)


class MarketDataProvider(Protocol):
    """Typed boundary; implementations must keep credentials server-side."""

    @property
    def provider_name(self) -> str:
        """Public, non-secret provider label."""
        ...

    def healthcheck(self) -> bool:
        """Return reachability using a bounded request timeout."""
        ...

    def get_market_data(self, request: MarketIntelligenceRequest) -> MarketDataSnapshot:
        """Return validated source records and derived insights.

        Implementations must use fixed provider hosts (never request input as a
        URL), enforce connect/read timeouts and response-byte limits, and avoid
        logging credentials or raw response payloads.
        """
        ...


class MarketDataProviderError(Exception):
    """Safe error raised when a configured provider fails contract validation."""


class MarketDataRateLimitedError(MarketDataProviderError):
    """Safe provider rate-limit result with a bounded retry hint."""

    def __init__(self, retry_after_seconds: int = 60) -> None:
        super().__init__("Configured market-data source is temporarily rate limited.")
        self.retry_after_seconds = max(1, min(int(retry_after_seconds), 3600))


@dataclass(frozen=True)
class _CachedSnapshot:
    stored_at: float
    snapshot: MarketDataSnapshot


class MarketIntelligenceService:
    def __init__(
        self,
        provider: MarketDataProvider | None = None,
        *,
        fresh_ttl_seconds: int = 15 * 60,
        stale_ttl_seconds: int = 24 * 60 * 60,
        max_cache_entries: int = 128,
        clock=time.monotonic,
    ) -> None:
        if fresh_ttl_seconds <= 0 or stale_ttl_seconds < fresh_ttl_seconds or max_cache_entries <= 0:
            raise ValueError("market cache limits must be positive and internally consistent")
        self._provider = provider
        self._fresh_ttl_seconds = fresh_ttl_seconds
        self._stale_ttl_seconds = stale_ttl_seconds
        self._max_cache_entries = max_cache_entries
        self._clock = clock
        self._cache: OrderedDict[tuple[str, str, str, tuple[str, ...]], _CachedSnapshot] = OrderedDict()
        self._cache_lock = threading.Lock()

    def configure_provider(self, provider: MarketDataProvider | None) -> None:
        """Change provider only during app lifecycle and discard cross-provider cache entries."""
        with self._cache_lock:
            self._provider = provider
            self._cache.clear()

    def provider_status(self) -> MarketProviderStatusResponse:
        if self._provider is None:
            return MarketProviderStatusResponse(
                provider=None,
                availability="not_configured",
                checked_at=None,
                message="No approved market-data provider is configured.",
            )
        checked_at = datetime.now(timezone.utc)
        name = str(getattr(self._provider, "provider_name", "Configured provider"))[:120]
        try:
            available = bool(self._provider.healthcheck())
        except Exception:
            available = False
        return MarketProviderStatusResponse(
            provider=name,
            availability="available" if available else "unavailable",
            checked_at=checked_at,
            message=("The configured provider is reachable." if available else "The configured provider is currently unavailable."),
        )

    def query(self, request: MarketIntelligenceRequest) -> MarketIntelligenceResponse:
        key = self._cache_key(request)
        cached = self._get_cache(key)
        age = self._clock() - cached.stored_at if cached else None
        if cached is not None and age is not None and age <= self._fresh_ttl_seconds:
            return self._build_response(request, cached.snapshot, "current", "Market insights are attributed to the verified sources listed below.")

        if self._provider is None:
            return self._unavailable(
                request,
                "No approved market-data provider is configured. No market insights are available for this request.",
            )

        try:
            snapshot = MarketDataSnapshot.model_validate(self._provider.get_market_data(request))
            response = self._build_response(
                request,
                snapshot,
                "current",
                "Market insights are attributed to the verified sources listed below.",
            )
            self._put_cache(key, snapshot)
            return response
        except MarketDataRateLimitedError as exc:
            failure = exc
        except (ValidationError, ValueError):
            failure = MarketDataProviderError("Configured market-data source returned invalid provenance or data.")
        except Exception:
            failure = MarketDataProviderError("Configured market-data source could not provide data.")

        if cached is not None and age is not None and age <= self._stale_ttl_seconds and cached.snapshot.sources and cached.snapshot.insights:
            return self._build_response(
                request,
                cached.snapshot,
                "stale",
                "The provider could not refresh this result. Showing previously retrieved data; review its source timestamps before use.",
            )
        raise failure from None

    @staticmethod
    def _cache_key(request: MarketIntelligenceRequest) -> tuple[str, str, str, tuple[str, ...]]:
        return (
            request.job_title.casefold(),
            (request.geography or "").casefold(),
            request.country_code,
            tuple(skill.casefold() for skill in request.skill_queries),
        )

    def _get_cache(self, key: tuple[str, str, str, tuple[str, ...]]) -> _CachedSnapshot | None:
        now = self._clock()
        with self._cache_lock:
            for old_key, entry in list(self._cache.items()):
                if now - entry.stored_at > self._stale_ttl_seconds:
                    self._cache.pop(old_key, None)
            entry = self._cache.get(key)
            if entry is not None:
                self._cache.move_to_end(key)
            return entry

    def _put_cache(self, key: tuple[str, str, str, tuple[str, ...]], snapshot: MarketDataSnapshot) -> None:
        with self._cache_lock:
            self._cache[key] = _CachedSnapshot(stored_at=self._clock(), snapshot=snapshot)
            self._cache.move_to_end(key)
            while len(self._cache) > self._max_cache_entries:
                self._cache.popitem(last=False)

    @staticmethod
    def _build_response(
        request: MarketIntelligenceRequest,
        snapshot: MarketDataSnapshot,
        freshness: str,
        message: str,
    ) -> MarketIntelligenceResponse:
        has_supported_data = bool(snapshot.sources and snapshot.insights)
        if not has_supported_data and freshness != "stale":
            message = "The configured source returned no supported market insights for this role and geography."
        return MarketIntelligenceResponse(
            job_title=request.job_title,
            geography=request.geography,
            data_availability="available" if has_supported_data else "unavailable",
            freshness=freshness if has_supported_data else "not_available",
            sources=snapshot.sources,
            insights=snapshot.insights,
            message=message,
        )

    @staticmethod
    def _unavailable(request: MarketIntelligenceRequest, message: str) -> MarketIntelligenceResponse:
        return MarketIntelligenceResponse(
            job_title=request.job_title,
            geography=request.geography,
            data_availability="unavailable",
            freshness="not_available",
            sources=[],
            insights=[],
            message=message,
        )


market_intelligence_service = MarketIntelligenceService()
