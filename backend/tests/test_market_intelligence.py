from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app import main
from app.market_intelligence import MarketDataProviderError, MarketIntelligenceService
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
        }],
    )


class FixtureProvider:
    def __init__(self, snapshot: MarketDataSnapshot) -> None:
        self.snapshot = snapshot
        self.requests: list[MarketIntelligenceRequest] = []

    def get_market_data(self, incoming: MarketIntelligenceRequest) -> MarketDataSnapshot:
        self.requests.append(incoming)
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
    assert first.job_title == "Data Engineer"
    assert first.geography == "Toronto, CA"
    assert first.sources == []
    assert first.insights == []
    assert "No verified market-data source is configured" in first.message


def test_endpoint_returns_typed_unavailable_structure_without_source():
    result = main.market_intelligence(request())
    serialized = result.model_dump()

    assert isinstance(result, MarketIntelligenceResponse)
    assert set(serialized) == {
        "job_title", "geography", "data_availability", "sources", "insights", "message",
    }
    assert serialized["data_availability"] == "unavailable"
    assert serialized["sources"] == []
    assert serialized["insights"] == []
    assert "No market insights" in serialized["message"]
    operation = main.app.openapi()["paths"]["/api/market/intelligence"]["post"]
    assert "422" in operation["responses"]


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


def test_provider_without_supported_insights_returns_unavailable_not_empty_claims():
    provider = FixtureProvider(MarketDataSnapshot(sources=[sourced_snapshot().sources[0]], insights=[]))

    result = MarketIntelligenceService(provider).query(request())

    assert result.data_availability == "unavailable"
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


def test_unavailable_response_cannot_contain_market_insights():
    snapshot = sourced_snapshot()
    with pytest.raises(ValidationError):
        MarketIntelligenceResponse(
            job_title="Data Engineer",
            geography=None,
            data_availability="unavailable",
            sources=snapshot.sources,
            insights=snapshot.insights,
            message="Unavailable",
        )
