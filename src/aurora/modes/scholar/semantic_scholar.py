"""Optional Semantic Scholar metadata enrichment for scholar papers."""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Any
from urllib.parse import quote

import httpx

from aurora.config import SemanticScholarSourceConfig
from aurora.models import SignalItem


SEMANTIC_SCHOLAR_API_URL = "https://api.semanticscholar.org/graph/v1"
FIELDS = "paperId,url,externalIds,citationCount,influentialCitationCount"


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

    async def enrich_items(self, items: Sequence[SignalItem]) -> list[SignalItem]:
        if not self.is_configured() or self.config.max_requests_per_run == 0:
            return list(items)
        enriched: list[SignalItem] = []
        request_count = 0
        for item in items:
            if request_count >= self.config.max_requests_per_run:
                enriched.append(item)
                continue
            paper_id = _lookup_id(item)
            if paper_id is None:
                enriched.append(item)
                continue
            request_count += 1
            metadata = await self._fetch_metadata(paper_id)
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
