"""Optional Semantic Scholar metadata enrichment for scholar papers."""

from __future__ import annotations

import os
from collections.abc import Sequence
from json import JSONDecodeError
from typing import Any
from urllib.parse import quote

import httpx

from aurora.config import SemanticScholarSourceConfig
from aurora.models import SignalItem
from aurora.pipeline import StageContext


SEMANTIC_SCHOLAR_API_URL = "https://api.semanticscholar.org/graph/v1"
FIELDS = "paperId,url,externalIds,citationCount,influentialCitationCount"
RATE_LIMIT_WARNING = (
    "Semantic Scholar enrichment rate-limited; deterministic scholar scoring used."
)
FAILURE_WARNING = (
    "Semantic Scholar enrichment skipped for one or more papers; deterministic scholar scoring used."
)


class SemanticScholarClient:
    """Small client for optional Semantic Scholar paper metadata lookup."""

    def __init__(
        self,
        config: SemanticScholarSourceConfig,
        *,
        http_client: httpx.AsyncClient,
        base_url: str = SEMANTIC_SCHOLAR_API_URL,
    ) -> None:
        self.config = config
        self.http_client = http_client
        self.base_url = base_url.rstrip("/")

    def is_configured(self) -> bool:
        return self.config.enabled and bool(os.getenv(self.config.api_key_env))

    async def enrich_items(
        self, items: Sequence[SignalItem], context: StageContext | None = None
    ) -> list[SignalItem]:
        if not self.is_configured() or self.config.max_requests_per_run == 0:
            return list(items)
        enriched: list[SignalItem] = []
        request_count = 0
        rate_limited = False
        for item in items:
            if rate_limited or request_count >= self.config.max_requests_per_run:
                enriched.append(item)
                continue
            paper_id = _lookup_id(item)
            if paper_id is None:
                enriched.append(item)
                continue
            request_count += 1
            try:
                metadata = await self._fetch_metadata(paper_id)
            except (httpx.HTTPError, JSONDecodeError, ValueError, TypeError) as exc:
                rate_limited = _is_rate_limit_error(exc)
                _record_failure(context, item, paper_id, exc, rate_limited=rate_limited)
                enriched.append(item)
                continue
            enriched.append(_apply_metadata(item, metadata) if metadata else item)
        return enriched

    async def _fetch_metadata(self, paper_id: str) -> dict[str, Any] | None:
        response = await self.http_client.get(
            f"{self.base_url}/paper/{quote(paper_id, safe=':')}",
            params={"fields": FIELDS},
            headers={
                "User-Agent": "Aurora-Scholar/0.1",
                "x-api-key": os.environ[self.config.api_key_env],
            },
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else None


def _lookup_id(item: SignalItem) -> str | None:
    metadata = item.metadata
    source_ids = metadata.get("source_ids") if isinstance(metadata.get("source_ids"), dict) else {}
    semantic_scholar_id = source_ids.get("semantic_scholar") or metadata.get(
        "semantic_scholar_paper_id"
    )
    if semantic_scholar_id:
        return str(semantic_scholar_id)
    doi = source_ids.get("doi") or metadata.get("doi")
    if doi:
        return f"DOI:{doi}"
    arxiv = source_ids.get("arxiv")
    if arxiv:
        return f"ARXIV:{arxiv}"
    return None


def _apply_metadata(item: SignalItem, payload: dict[str, Any]) -> SignalItem:
    metadata = dict(item.metadata)
    source_ids = dict(metadata.get("source_ids") or {})
    paper_id = payload.get("paperId")
    external_ids = payload.get("externalIds") if isinstance(payload.get("externalIds"), dict) else {}
    if paper_id:
        metadata["semantic_scholar_paper_id"] = str(paper_id)
        source_ids["semantic_scholar"] = str(paper_id)
    if payload.get("url"):
        metadata["semantic_scholar_url"] = str(payload["url"])
    if "citationCount" in payload:
        metadata["citation_count"] = int(payload.get("citationCount") or 0)
    if "influentialCitationCount" in payload:
        metadata["influential_citation_count"] = int(
            payload.get("influentialCitationCount") or 0
        )
    if external_ids.get("DOI"):
        source_ids["doi"] = str(external_ids["DOI"])
    if external_ids.get("ArXiv"):
        source_ids["arxiv"] = str(external_ids["ArXiv"])
    metadata["source_ids"] = source_ids
    return item.model_copy(update={"metadata": metadata})


def _is_rate_limit_error(exc: Exception) -> bool:
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429


def _record_failure(
    context: StageContext | None,
    item: SignalItem,
    paper_id: str,
    exc: Exception,
    *,
    rate_limited: bool,
) -> None:
    if context is None:
        return
    context.metadata["semantic_scholar_enrichment_failed_count"] = (
        int(context.metadata.get("semantic_scholar_enrichment_failed_count") or 0) + 1
    )
    if rate_limited:
        context.metadata["semantic_scholar_rate_limited"] = True
    warning = RATE_LIMIT_WARNING if rate_limited else FAILURE_WARNING
    warnings = context.metadata.setdefault("semantic_scholar_warnings", [])
    if isinstance(warnings, list) and warning not in warnings:
        warnings.append(warning)
    failures = context.metadata.setdefault("semantic_scholar_failures", [])
    if isinstance(failures, list):
        failures.append(
            {
                "item_id": item.id,
                "paper_id": paper_id,
                "status_code": _status_code(exc),
                "rate_limited": rate_limited,
                "error": _error_summary(exc),
            }
        )


def _status_code(exc: Exception) -> int | None:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code
    return None


def _error_summary(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code} {exc.response.reason_phrase}".strip()
    if isinstance(exc, httpx.RequestError):
        return exc.__class__.__name__
    if isinstance(exc, JSONDecodeError):
        return "invalid JSON response"
    return exc.__class__.__name__
