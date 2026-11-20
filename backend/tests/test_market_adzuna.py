import httpx
import pytest

from app.market_adzuna import AdzunaMarketDataProvider, configured_market_provider
from app.market_intelligence import MarketDataRateLimitedError
from app.models import MarketIntelligenceRequest


def test_adzuna_adapter_returns_attributed_role_and_entered_keyword_counts_without_extrapolation():
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        count = 148 if "what_and" not in request.url.params else 42
        return httpx.Response(200, json={"count": count, "results": []})

    provider = AdzunaMarketDataProvider("synthetic-id", "synthetic-key", transport=httpx.MockTransport(respond))
    result = provider.get_market_data(MarketIntelligenceRequest(
        job_title="Data Engineer", geography="Bengaluru", country_code="in", skill_queries=["Python"],
    ))

    assert len(requests) == 2
    assert all(request.url.scheme == "https" and request.url.host == "api.adzuna.com" for request in requests)
    assert all(request.url.path == "/v1/api/jobs/in/search/1" for request in requests)
    assert requests[0].url.params["what_phrase"] == "Data Engineer"
    assert requests[0].url.params["where"] == "Bengaluru"
    assert "what_and" not in requests[0].url.params
    assert requests[1].url.params["what_phrase"] == "Data Engineer"
    assert requests[1].url.params["what_and"] == "Python"
    assert requests[0].url.params["app_id"] == "synthetic-id"
    assert requests[0].url.params["app_key"] == "synthetic-key"
    assert [insight.value for insight in result.insights] == [148, 42]
    assert [insight.category for insight in result.insights] == ["role_demand", "skill_demand"]
    assert all("count field" in insight.calculation_method for insight in result.insights)
    assert result.sources[0].geography == "India"
    assert "synthetic-key" not in result.sources[0].source_reference


@pytest.mark.parametrize("payload", [{}, {"count": -1}, {"count": True}, {"count": "12"}])
def test_adzuna_adapter_rejects_missing_or_invalid_count_without_claiming_data(payload):
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, json=payload))
    provider = AdzunaMarketDataProvider("synthetic-id", "synthetic-key", transport=transport)
    with pytest.raises(ValueError, match="valid result count"):
        provider.get_market_data(MarketIntelligenceRequest(job_title="Data Engineer"))


def test_adzuna_adapter_bounds_response_size_and_hides_upstream_details():
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, content=b"{" + (b"x" * 600_000)))
    provider = AdzunaMarketDataProvider("synthetic-id", "synthetic-secret-key", transport=transport)
    with pytest.raises(RuntimeError, match="Adzuna market-data request failed") as error:
        provider.get_market_data(MarketIntelligenceRequest(job_title="Data Engineer"))
    assert "synthetic-secret-key" not in str(error.value)


def test_adzuna_adapter_uses_fixed_host_and_safe_errors_for_upstream_rate_limits():
    request_urls: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        request_urls.append(str(request.url))
        return httpx.Response(429, text="private response details")

    provider = AdzunaMarketDataProvider("synthetic-id", "synthetic-key", transport=httpx.MockTransport(respond))
    with pytest.raises(MarketDataRateLimitedError) as error:
        provider.get_market_data(MarketIntelligenceRequest(job_title="Data Engineer"))
    assert error.value.retry_after_seconds == 60
    assert "private response details" not in str(error.value)
    assert "synthetic-key" not in str(error.value)
    assert request_urls[0].startswith("https://api.adzuna.com/v1/api/jobs/in/search/1?")


def test_provider_configuration_fails_closed_without_selection_credentials_and_usage_approval(monkeypatch):
    for name in ("SKILLFAIR_MARKET_PROVIDER", "SKILLFAIR_ADZUNA_APP_ID", "SKILLFAIR_ADZUNA_APP_KEY", "SKILLFAIR_ADZUNA_USAGE_APPROVED"):
        monkeypatch.delenv(name, raising=False)
    assert configured_market_provider() is None

    monkeypatch.setenv("SKILLFAIR_MARKET_PROVIDER", "adzuna")
    monkeypatch.setenv("SKILLFAIR_ADZUNA_APP_ID", "synthetic-id")
    monkeypatch.setenv("SKILLFAIR_ADZUNA_APP_KEY", "synthetic-key")
    assert configured_market_provider() is None

    monkeypatch.setenv("SKILLFAIR_ADZUNA_USAGE_APPROVED", "true")
    assert isinstance(configured_market_provider(), AdzunaMarketDataProvider)


def test_country_code_and_skill_query_input_are_validated_and_deduplicated():
    request = MarketIntelligenceRequest(
        job_title="Data Engineer", country_code="in", skill_queries=[" Python ", "python", "SQL"],
    )
    assert request.skill_queries == ["Python", "SQL"]
    with pytest.raises(ValueError):
        MarketIntelligenceRequest(job_title="Data Engineer", country_code="xx")
    with pytest.raises(ValueError):
        MarketIntelligenceRequest(job_title="Data Engineer", skill_queries=["x" * 101])
    with pytest.raises(ValueError):
        MarketIntelligenceRequest(job_title="Data Engineer", skill_queries=["SQL"] * 6)
