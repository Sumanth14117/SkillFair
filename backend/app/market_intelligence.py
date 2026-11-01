from typing import Protocol

from pydantic import ValidationError

from .models import (
    MarketDataSnapshot,
    MarketIntelligenceRequest,
    MarketIntelligenceResponse,
)


class MarketDataProvider(Protocol):
    """Boundary for a future configured provider with verified source provenance."""

    def get_market_data(self, request: MarketIntelligenceRequest) -> MarketDataSnapshot:
        ...


class MarketDataProviderError(Exception):
    """Safe error raised when a configured provider fails contract validation."""


class MarketIntelligenceService:
    def __init__(self, provider: MarketDataProvider | None = None) -> None:
        self._provider = provider

    def query(self, request: MarketIntelligenceRequest) -> MarketIntelligenceResponse:
        if self._provider is None:
            return self._unavailable(
                request,
                "No verified market-data source is configured. No market insights are available for this request.",
            )

        try:
            snapshot = MarketDataSnapshot.model_validate(self._provider.get_market_data(request))
            return MarketIntelligenceResponse(
                job_title=request.job_title,
                geography=request.geography,
                data_availability="available" if snapshot.sources and snapshot.insights else "unavailable",
                sources=snapshot.sources,
                insights=snapshot.insights,
                message=(
                    "Market insights are attributed to the verified sources listed below."
                    if snapshot.sources and snapshot.insights
                    else "The configured source returned no supported market insights for this role and geography."
                ),
            )
        except (ValidationError, ValueError) as exc:
            raise MarketDataProviderError("Configured market-data source returned invalid provenance or data.") from exc
        except Exception as exc:
            raise MarketDataProviderError("Configured market-data source could not provide data.") from exc

    @staticmethod
    def _unavailable(
        request: MarketIntelligenceRequest,
        message: str,
    ) -> MarketIntelligenceResponse:
        return MarketIntelligenceResponse(
            job_title=request.job_title,
            geography=request.geography,
            data_availability="unavailable",
            sources=[],
            insights=[],
            message=message,
        )


market_intelligence_service = MarketIntelligenceService()
