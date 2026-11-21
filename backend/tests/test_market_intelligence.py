from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app import main
from app.market_intelligence import MarketDataProviderError, MarketDataRateLimitedError, MarketIntelligenceService
from app.models import (
    MarketDataSnapshot,
    MarketIntelligenceRequest,
    MarketIntelligenceResponse,
)


def request(**overrides) -> MarketIntelligenceRequest:
    values = {"job_title": "Data Engineer"}
    values.update(overrides)
    return MarketIntelligenceRequest(**values)


def sourced_snapshot(**source_overrides) -> MarketDataSnapshot:
    source = {
        "source_id": "verified-fixture",
        "source_name": "Test verified dataset",
        "source_type": "dataset",
        "verification_status": "verified",
        "retrieved_at": datetime(2026, 10, 30, 10, 0, tzinfo=timezone.utc),
        "published_at": datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc),
        "geography": "Global",
        "source_reference": "fixture:market-data-test",
    }
    source.update(source_overrides)
    return MarketDataSnapshot(
        sources=[source],
        insights=[{
            "insight_id": "insight-1",
            "category": "skill_demand",
            "title": "Example supported insight",
            "summary": "Test-only content returned by an explicitly supplied fixture provider.",
            "value": 12.5,
            "unit": "percent",
            "period": "2026-Q3",
            "geography": "Global",
            "source_id": "verified-fixture",
            "calculation_method": "Test fixture value supplied directly; no calculation is performed.",
            "data_quality": "limited",
        }],
    )


class FixtureProvider:
    def __init__(self, snapshot: MarketDataSnapshot) -> None:
        self.snapshot = snapshot
        self.requests: list[MarketIntelligenceRequest] = []
        self.failure: Exception | None = None

    @property
    def provider_name(self) -> str:
        return "Fixture provider"

    def healthcheck(self) -> bool:
        return self.failure is None

    def get_market_data(self, incoming: MarketIntelligenceRequest) -> MarketDataSnapshot:
        self.requests.append(incoming)
        if self.failure is not None:
            raise self.failure
        return self.snapshot


def test_market_request_strips_valid_role_and_geography():
    result = request(job_title="  Product Analyst  ", geography="  Bengaluru, IN ")

    assert result.job_title == "Product Analyst"
    assert result.geography == "Bengaluru, IN"
    assert request(geography=None).geography is None


@pytest.mark.parametrize("job_title", ["", "   ", "x" * 161])
def test_market_request_rejects_empty_or_oversized_job_title(job_title):
    with pytest.raises(ValidationError):
        request(job_title=job_title)


@pytest.mark.parametrize("geography", ["", "   ", "x" * 121])
def test_market_request_rejects_empty_or_oversized_geography(geography):
    with pytest.raises(ValidationError):
        request(geography=geography)


def test_unconfigured_provider_returns_deterministic_unavailable_response():
    service = MarketIntelligenceService()
    submitted = request(geography="Toronto, CA")

    first = service.query(submitted)
    second = service.query(submitted)

    assert first == second
    assert first.data_availability == "unavailable"
    assert first.freshness == "not_available"
    assert first.job_title == "Data Engineer"
    assert first.geography == "Toronto, CA"
    assert first.sources == []
    assert first.insights == []
    assert "No approved market-data provider is configured" in first.message


def test_endpoint_returns_typed_unavailable_structure_without_source():
    result = main.market_intelligence(request())
    serialized = result.model_dump()

    assert isinstance(result, MarketIntelligenceResponse)
    assert set(serialized) == {
        "job_title", "geography", "data_availability", "sources", "insights", "message",
        "freshness",
    }
    assert serialized["data_availability"] == "unavailable"
    assert serialized["freshness"] == "not_available"
    assert serialized["sources"] == []
    assert serialized["insights"] == []
    assert "No market insights" in serialized["message"]
    operation = main.app.openapi()["paths"]["/api/market/intelligence"]["post"]
    assert "422" in operation["responses"]
    status = main.market_provider_status()
    assert status.provider is None
    assert status.availability == "not_configured"
    assert status.checked_at is None


def test_configured_verified_provider_returns_attributed_insights():
    provider = FixtureProvider(sourced_snapshot())
    service = MarketIntelligenceService(provider)
    submitted = request(geography="Global")

    result = service.query(submitted)

    assert provider.requests == [submitted]
    assert result.data_availability == "available"
    assert result.sources[0].verification_status == "verified"
    assert result.sources[0].retrieved_at == datetime(2026, 10, 30, 10, 0, tzinfo=timezone.utc)
    assert result.insights[0].source_id == result.sources[0].source_id
    assert result.insights[0].period == "2026-Q3"
    assert result.insights[0].calculation_method
    assert result.insights[0].data_quality == "limited"
    assert result.freshness == "current"


def test_provider_without_supported_insights_returns_unavailable_not_empty_claims():
    provider = FixtureProvider(MarketDataSnapshot(sources=[sourced_snapshot().sources[0]], insights=[]))

    result = MarketIntelligenceService(provider).query(request())

    assert result.data_availability == "unavailable"
    assert result.freshness == "not_available"
    assert len(result.sources) == 1
    assert result.insights == []
    assert "no supported market insights" in result.message


def test_unverified_sources_and_unattributed_insights_are_rejected():
    with pytest.raises(ValidationError):
        sourced_snapshot(verification_status="unverified")

    invalid_value = sourced_snapshot().model_dump()
    invalid_value["insights"][0]["value"] = float("nan")
    with pytest.raises(ValidationError):
        MarketDataSnapshot.model_validate(invalid_value)

    snapshot = sourced_snapshot()
    snapshot.insights[0].source_id = "unknown-source"
    with pytest.raises(MarketDataProviderError):
        MarketIntelligenceService(FixtureProvider(snapshot)).query(request())

    invalid_source = sourced_snapshot().model_dump()
    invalid_source["sources"][0]["retrieved_at"] = datetime(2026, 10, 30, 10, 0)
    with pytest.raises(ValidationError):
        MarketDataSnapshot.model_validate(invalid_source)

    invalid_source = sourced_snapshot().model_dump()
    invalid_source["sources"][0]["published_at"] = datetime(2026, 11, 1, tzinfo=timezone.utc)
    with pytest.raises(ValidationError):
        MarketDataSnapshot.model_validate(invalid_source)


def test_market_insight_requires_observation_period_method_and_quality():
    incomplete = sourced_snapshot().model_dump()
    del incomplete["insights"][0]["calculation_method"]
    with pytest.raises(ValidationError):
        MarketDataSnapshot.model_validate(incomplete)

    incomplete = sourced_snapshot().model_dump()
    incomplete["insights"][0]["period"] = "   "
    with pytest.raises(ValidationError):
        MarketDataSnapshot.model_validate(incomplete)


class MutableClock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value


def test_fresh_market_snapshot_is_cached_within_the_bounded_freshness_window():
    provider = FixtureProvider(sourced_snapshot())
    clock = MutableClock()
    service = MarketIntelligenceService(provider, fresh_ttl_seconds=10, stale_ttl_seconds=30, clock=clock)

    first = service.query(request())
    clock.value += 5
    second = service.query(request())

    assert first.freshness == second.freshness == "current"
    assert len(provider.requests) == 1


def test_provider_outage_serves_attributed_stale_snapshot_with_warning():
    provider = FixtureProvider(sourced_snapshot())
    clock = MutableClock()
    service = MarketIntelligenceService(provider, fresh_ttl_seconds=10, stale_ttl_seconds=30, clock=clock)
    first = service.query(request())
    provider.failure = TimeoutError("private provider details")
    clock.value += 12

    stale = service.query(request())

    assert stale.data_availability == "available"
    assert stale.freshness == "stale"
    assert stale.sources == first.sources
    assert stale.insights == first.insights
    assert "could not refresh" in stale.message
    assert "private provider details" not in stale.message


def test_rate_limit_without_stale_snapshot_preserves_safe_retry_hint():
    provider = FixtureProvider(sourced_snapshot())
    provider.failure = MarketDataRateLimitedError(75)
    service = MarketIntelligenceService(provider)

    with pytest.raises(MarketDataRateLimitedError) as error:
        service.query(request())

    assert error.value.retry_after_seconds == 75
    assert str(error.value) == "Configured market-data source is temporarily rate limited."


def test_market_endpoint_maps_rate_limit_to_safe_503_and_retry_after(monkeypatch):
    provider = FixtureProvider(sourced_snapshot())
    provider.failure = MarketDataRateLimitedError(75)
    monkeypatch.setattr(main, "market_intelligence_service", MarketIntelligenceService(provider))

    with pytest.raises(main.HTTPException) as error:
        main.market_intelligence(request())

    assert error.value.status_code == 503
    assert error.value.headers == {"Retry-After": "75"}
    assert error.value.detail == "The configured market-data source is temporarily rate limited. Try again later."


@pytest.mark.parametrize("failure", [TimeoutError("timed out"), ConnectionError("offline"), RuntimeError("rate limit response")])
def test_provider_failures_without_a_stale_snapshot_raise_a_safe_error(failure):
    provider = FixtureProvider(sourced_snapshot())
    provider.failure = failure

    with pytest.raises(MarketDataProviderError) as error:
        MarketIntelligenceService(provider).query(request())

    assert str(error.value) == "Configured market-data source could not provide data."
    assert "timed out" not in str(error.value)


def test_provider_health_reports_availability_without_leaking_failure_details():
    provider = FixtureProvider(sourced_snapshot())
    service = MarketIntelligenceService(provider)
    assert service.provider_status().availability == "available"
    provider.failure = RuntimeError("private failure detail")
    result = service.provider_status()
    assert result.provider == "Fixture provider"
    assert result.availability == "unavailable"
    assert "private failure detail" not in result.message


def test_unavailable_response_cannot_contain_market_insights():
    snapshot = sourced_snapshot()
    with pytest.raises(ValidationError):
        MarketIntelligenceResponse(
            job_title="Data Engineer",
            geography=None,
            data_availability="unavailable",
            freshness="current",
            sources=snapshot.sources,
            insights=snapshot.insights,
            message="Unavailable",
        )
