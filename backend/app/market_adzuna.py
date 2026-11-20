"""Approval-gated Adzuna adapter for current role and keyword ad counts.

This adapter does not infer skill trends, related skills, or candidate gaps. It is
disabled unless the operator explicitly selects the provider, supplies server-side
credentials, and confirms the required usage approval.
"""

from datetime import datetime, timezone
import json
import os
from typing import Any

import httpx

from .market_intelligence import MarketDataRateLimitedError
from .models import MarketDataSnapshot, MarketIntelligenceRequest


ADZUNA_API_BASE = "https://api.adzuna.com/v1/api"
ADZUNA_SOURCE_URL = "https://developer.adzuna.com/overview"
MAX_PROVIDER_RESPONSE_BYTES = 512 * 1024
CONNECT_TIMEOUT_SECONDS = 3.0
READ_TIMEOUT_SECONDS = 8.0
COUNTRY_NAMES = {
    "gb": "United Kingdom", "us": "United States", "at": "Austria", "au": "Australia",
    "be": "Belgium", "br": "Brazil", "ca": "Canada", "ch": "Switzerland",
    "de": "Germany", "es": "Spain", "fr": "France", "in": "India",
    "it": "Italy", "mx": "Mexico", "nl": "Netherlands", "nz": "New Zealand",
    "pl": "Poland", "sg": "Singapore", "za": "South Africa",
}


class AdzunaMarketDataProvider:
    provider_name = "Adzuna job-ad search"

    def __init__(self, app_id: str, app_key: str, *, transport: httpx.BaseTransport | None = None) -> None:
        if not app_id.strip() or not app_key.strip():
            raise ValueError("Adzuna credentials are required")
        self._app_id = app_id.strip()
        self._app_key = app_key.strip()
        self._transport = transport

    def healthcheck(self) -> bool:
        try:
            self._get_json("version", "in")
            return True
        except Exception:
            return False

    def get_market_data(self, request: MarketIntelligenceRequest) -> MarketDataSnapshot:
        retrieved_at = datetime.now(timezone.utc)
        country_name = COUNTRY_NAMES[request.country_code]
        role_data = self._search(request)
        if "count" not in role_data or not _is_nonnegative_integer(role_data["count"]):
            raise ValueError("Adzuna response did not include a valid result count")

        source_id = "adzuna-current-job-ads"
        period = f"Snapshot {retrieved_at.strftime('%Y-%m-%d %H:%M UTC')}"
        insights: list[dict[str, Any]] = [{
            "insight_id": "role-demand-current-ad-count",
            "category": "role_demand",
            "title": f"Current job ads matching {request.job_title}",
            "summary": "Current indexed advertisements returned for the role-title query. This is a provider search count, not a labor-market census.",
            "value": role_data["count"],
            "unit": "matching job ads",
            "period": period,
            "geography": request.geography or country_name,
            "source_id": source_id,
            "calculation_method": "The Adzuna Search API count field for the submitted role-title phrase and selected country/location; no extrapolation or deduplication is applied by SkillFair.",
            "data_quality": "limited",
        }]

        for index, skill in enumerate(request.skill_queries, start=1):
            skill_data = self._search(request, skill=skill)
            if "count" not in skill_data or not _is_nonnegative_integer(skill_data["count"]):
                raise ValueError("Adzuna response did not include a valid result count")
            insights.append({
                "insight_id": f"role-skill-keyword-{index}",
                "category": "skill_demand",
                "title": f"Job ads matching role and “{skill}”",
                "summary": "Current indexed advertisements matching both the role-title phrase and this recruiter-entered keyword. This is not a measure of candidate proficiency or an independently verified skill requirement.",
                "value": skill_data["count"],
                "unit": "matching job ads",
                "period": period,
                "geography": request.geography or country_name,
                "source_id": source_id,
                "calculation_method": "The Adzuna Search API count field using the role-title phrase plus the submitted skill phrase as required keywords; no extrapolation or deduplication is applied by SkillFair.",
                "data_quality": "limited",
            })

        return MarketDataSnapshot(sources=[{
            "source_id": source_id,
            "source_name": "Adzuna API",
            "source_type": "job-ad search index",
            "verification_status": "verified",
            "retrieved_at": retrieved_at,
            "published_at": None,
            "geography": country_name,
            "source_reference": ADZUNA_SOURCE_URL,
        }], insights=insights)

    def _search(self, request: MarketIntelligenceRequest, *, skill: str | None = None) -> dict[str, Any]:
        params: dict[str, str | int] = {
            "app_id": self._app_id,
            "app_key": self._app_key,
            "content-type": "application/json",
            "results_per_page": 1,
        }
        if skill is None:
            params["what_phrase"] = request.job_title
        else:
            params["what_phrase"] = request.job_title
            params["what_and"] = skill
        if request.geography:
            params["where"] = request.geography
        return self._get_json(f"jobs/{request.country_code}/search/1", params)

    def _get_json(self, path: str, country_or_params: str | dict[str, str | int]) -> dict[str, Any]:
        params = country_or_params if isinstance(country_or_params, dict) else {
            "app_id": self._app_id,
            "app_key": self._app_key,
            "content-type": "application/json",
        }
        url = f"{ADZUNA_API_BASE}/{path}"
        timeout = httpx.Timeout(connect=CONNECT_TIMEOUT_SECONDS, read=READ_TIMEOUT_SECONDS, write=CONNECT_TIMEOUT_SECONDS, pool=CONNECT_TIMEOUT_SECONDS)
        try:
            with httpx.Client(timeout=timeout, follow_redirects=False, transport=self._transport) as client:
                with client.stream("GET", url, params=params, headers={"Accept": "application/json"}) as response:
                    if response.status_code == 429:
                        try:
                            retry_after = int(response.headers.get("Retry-After", "60"))
                        except (TypeError, ValueError):
                            retry_after = 60
                        raise MarketDataRateLimitedError(retry_after)
                    if response.status_code != 200:
                        raise ValueError("upstream request failed")
                    chunks: list[bytes] = []
                    total = 0
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > MAX_PROVIDER_RESPONSE_BYTES:
                            raise ValueError("upstream response exceeded size limit")
                        chunks.append(chunk)
            payload = json.loads(b"".join(chunks))
            if not isinstance(payload, dict):
                raise ValueError("upstream response was not an object")
            return payload
        except MarketDataRateLimitedError:
            raise
        except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
            raise RuntimeError("Adzuna market-data request failed") from exc


def _is_nonnegative_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def configured_market_provider() -> AdzunaMarketDataProvider | None:
    """Fail closed unless operator configuration and permitted-use attestation exist."""
    if os.getenv("SKILLFAIR_MARKET_PROVIDER", "none").strip().casefold() != "adzuna":
        return None
    if os.getenv("SKILLFAIR_ADZUNA_USAGE_APPROVED", "").strip().casefold() != "true":
        return None
    app_id = os.getenv("SKILLFAIR_ADZUNA_APP_ID", "").strip()
    app_key = os.getenv("SKILLFAIR_ADZUNA_APP_KEY", "").strip()
    if not app_id or not app_key:
        return None
    return AdzunaMarketDataProvider(app_id, app_key)
