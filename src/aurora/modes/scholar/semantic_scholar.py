"""Optional Semantic Scholar metadata enrichment for scholar papers."""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from json import JSONDecodeError
import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from aurora.config import SemanticScholarSourceConfig
from aurora.models import SignalItem
from aurora.pipeline import StageContext


SEMANTIC_SCHOLAR_API_URL = "https://api.semanticscholar.org/graph/v1"
FIELDS = (
    "paperId,title,abstract,url,externalIds,citationCount,influentialCitationCount,"
    "venue,year,fieldsOfStudy,authors,openAccessPdf,tldr"
)
RATE_LIMIT_WARNING = (
    "Semantic Scholar enrichment rate-limited; deterministic scholar scoring used."
)
FAILURE_WARNING = (
    "Semantic Scholar enrichment skipped for one or more papers; deterministic scholar scoring used."
)
TITLE_MATCH_THRESHOLD = 0.92
NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
CACHE_RELATIVE_PATH = Path("scholar") / "semantic_scholar_metadata.json"


@dataclass
class SemanticScholarStats:
    """Aggregate diagnostics for one Semantic Scholar enrichment pass."""

    enriched: int = 0
    cached: int = 0
    skipped: int = 0
    failed: int = 0
    requests_made: int = 0
    rate_limited: bool = False


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
        self.stats = SemanticScholarStats()
        self._cache_path: Path | None = None
        self._cache: dict[str, Any] = {"version": 1, "entries": {}}
        self._cache_loaded = False
        self._cache_dirty = False

    def is_configured(self) -> bool:
        return self.config.enabled and bool(os.getenv(self.config.api_key_env))

    async def enrich_items(
        self, items: Sequence[SignalItem], context: StageContext | None = None
    ) -> list[SignalItem]:
        self.stats = SemanticScholarStats()
        self._configure_cache(context)
        if not self.is_configured() or self.config.max_requests_per_run == 0:
            self.stats.skipped = len(items)
            _record_stats(context, self.stats)
            return list(items)
        enriched: list[SignalItem] = []
        rate_limited = False
        for item in items:
            if rate_limited or self.stats.requests_made >= self.config.max_requests_per_run:
                self.stats.skipped += 1
                enriched.append(item)
                continue

            cache_key = _cache_key(item)
            cached = self._get_cached(cache_key)
            if cached:
                self.stats.cached += 1
                enriched.append(_apply_metadata(item, cached))
                continue

            if not _has_lookup_signal(item):
                self.stats.skipped += 1
                enriched.append(item)
                continue
            try:
                metadata = await self._find_metadata(item)
            except (httpx.HTTPError, JSONDecodeError, ValueError, TypeError) as exc:
                rate_limited = _is_rate_limit_error(exc)
                if rate_limited:
                    self.stats.rate_limited = True
                self.stats.failed += 1
                _record_failure(context, item, _lookup_label(item), exc, rate_limited=rate_limited)
                enriched.append(item)
                continue
            if metadata:
                self.stats.enriched += 1
                self._store_cached(cache_key, metadata)
                enriched.append(_apply_metadata(item, metadata))
            else:
                self.stats.skipped += 1
                enriched.append(item)
        self._save_cache()
        _record_stats(context, self.stats)
        return enriched

    async def _find_metadata(self, item: SignalItem) -> dict[str, Any] | None:
        for paper_id in _lookup_ids(item):
            payload = await self._fetch_metadata(paper_id)
            if payload and _payload_matches_identifier(item, payload):
                return payload
        return await self._search_by_title(item)

    async def _fetch_metadata(self, paper_id: str) -> dict[str, Any] | None:
        return await self._get_json(
            f"/paper/{quote(paper_id, safe=':')}",
            params={"fields": FIELDS},
            not_found_ok=True,
        )

    async def _search_by_title(self, item: SignalItem) -> dict[str, Any] | None:
        payload = await self._get_json(
            "/paper/search",
            params={"query": item.title, "limit": "5", "fields": FIELDS},
            not_found_ok=True,
        )
        if not isinstance(payload, dict):
            return None
        for candidate in payload.get("data") or []:
            if isinstance(candidate, dict) and _title_match(item.title, str(candidate.get("title") or "")):
                return candidate
        return None

    async def _get_json(
        self, path: str, *, params: dict[str, str], not_found_ok: bool = False
    ) -> dict[str, Any] | None:
        if self.stats.requests_made >= self.config.max_requests_per_run:
            return None
        self.stats.requests_made += 1
        response = await self.http_client.get(
            f"{self.base_url}{path}",
            params=params,
            headers={
                "User-Agent": "Aurora-Scholar/0.1",
                "x-api-key": os.environ[self.config.api_key_env],
            },
        )
        if response.status_code == 404 and not_found_ok:
            return None
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else None

    def _configure_cache(self, context: StageContext | None) -> None:
        self._cache_path = (
            context.config.run.cache_dir / CACHE_RELATIVE_PATH
            if context is not None and context.config is not None
            else None
        )
        self._cache = {"version": 1, "entries": {}}
        self._cache_loaded = False
        self._cache_dirty = False

    def _load_cache(self) -> None:
        if self._cache_loaded:
            return
        self._cache_loaded = True
        if self._cache_path is None or not self._cache_path.exists():
            return
        try:
            payload = json.loads(self._cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if isinstance(payload, dict) and isinstance(payload.get("entries"), dict):
            self._cache = payload

    def _get_cached(self, cache_key: str) -> dict[str, Any] | None:
        self._load_cache()
        entries = self._cache.setdefault("entries", {})
        entry = entries.get(cache_key) if isinstance(entries, dict) else None
        if not isinstance(entry, dict):
            return None
        data = entry.get("data")
        cached_at = _parse_cached_at(entry.get("cached_at"))
        if not isinstance(data, dict) or cached_at is None:
            return None
        now = datetime.now(timezone.utc)
        if now - cached_at > timedelta(hours=self.config.cache_ttl_hours):
            return None
        return data

    def _store_cached(self, cache_key: str, data: dict[str, Any]) -> None:
        self._load_cache()
        entries = self._cache.setdefault("entries", {})
        if not isinstance(entries, dict):
            entries = {}
            self._cache["entries"] = entries
        entries[cache_key] = {
            "cached_at": datetime.now(timezone.utc).isoformat(),
            "data": data,
        }
        self._cache_dirty = True

    def _save_cache(self) -> None:
        if self._cache_path is None or not self._cache_dirty:
            return
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache_path.write_text(
            json.dumps(self._cache, indent=2, sort_keys=True),
            encoding="utf-8",
        )


def _lookup_ids(item: SignalItem) -> list[str]:
    metadata = item.metadata
    source_ids = metadata.get("source_ids") if isinstance(metadata.get("source_ids"), dict) else {}
    ids: list[str] = []
    semantic_scholar_id = source_ids.get("semantic_scholar") or metadata.get(
        "semantic_scholar_paper_id"
    )
    if semantic_scholar_id:
        ids.append(str(semantic_scholar_id))
    doi = source_ids.get("doi") or metadata.get("doi")
    if doi:
        ids.append(f"DOI:{doi}")
    arxiv = source_ids.get("arxiv")
    if arxiv:
        ids.append(f"ARXIV:{arxiv}")
    return ids


def _has_lookup_signal(item: SignalItem) -> bool:
    return bool(_lookup_ids(item) or item.title.strip())


def _lookup_label(item: SignalItem) -> str:
    ids = _lookup_ids(item)
    return ids[0] if ids else f"title:{item.title[:80]}"


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
    if external_ids.get("CorpusId"):
        source_ids["corpus_id"] = str(external_ids["CorpusId"])
    if payload.get("venue") and not metadata.get("venue"):
        metadata["venue"] = str(payload["venue"])
    if payload.get("year") is not None and not metadata.get("venue_year"):
        metadata["venue_year"] = int(payload.get("year") or 0)
    fields = [str(value) for value in payload.get("fieldsOfStudy") or [] if value]
    if fields and not metadata.get("topics"):
        metadata["topics"] = fields
    authors = _author_names(payload.get("authors") or [])
    if authors and not metadata.get("authors"):
        metadata["authors"] = authors
    open_access_pdf = payload.get("openAccessPdf")
    if isinstance(open_access_pdf, dict) and open_access_pdf.get("url") and not metadata.get("pdf_url"):
        metadata["pdf_url"] = str(open_access_pdf["url"])
    tldr = payload.get("tldr")
    if isinstance(tldr, dict) and tldr.get("text"):
        metadata["semantic_scholar_tldr"] = str(tldr["text"])
    elif isinstance(tldr, str) and tldr.strip():
        metadata["semantic_scholar_tldr"] = tldr.strip()
    metadata["semantic_scholar"] = _raw_metadata(payload)
    metadata["source_ids"] = source_ids
    return item.model_copy(update={"metadata": metadata})


def _raw_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    tldr = payload.get("tldr")
    return {
        "paperId": payload.get("paperId"),
        "title": payload.get("title"),
        "abstract": payload.get("abstract"),
        "citationCount": payload.get("citationCount"),
        "influentialCitationCount": payload.get("influentialCitationCount"),
        "venue": payload.get("venue"),
        "year": payload.get("year"),
        "fieldsOfStudy": payload.get("fieldsOfStudy") or [],
        "authors": payload.get("authors") or [],
        "externalIds": payload.get("externalIds") or {},
        "openAccessPdf": payload.get("openAccessPdf"),
        "tldr": tldr.get("text") if isinstance(tldr, dict) else tldr,
        "url": payload.get("url"),
    }


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


def _record_stats(context: StageContext | None, stats: SemanticScholarStats) -> None:
    if context is None:
        return
    context.metadata["semantic_scholar_enriched_count"] = stats.enriched
    context.metadata["semantic_scholar_cached_count"] = stats.cached
    context.metadata["semantic_scholar_skipped_count"] = stats.skipped
    context.metadata["semantic_scholar_failed_count"] = stats.failed
    context.metadata["semantic_scholar_requests_made"] = stats.requests_made
    if stats.rate_limited:
        context.metadata["semantic_scholar_rate_limited"] = True


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


def _payload_matches_identifier(item: SignalItem, payload: dict[str, Any]) -> bool:
    source_ids = item.metadata.get("source_ids") if isinstance(item.metadata.get("source_ids"), dict) else {}
    external_ids = payload.get("externalIds") if isinstance(payload.get("externalIds"), dict) else {}
    arxiv = source_ids.get("arxiv")
    if arxiv and _canonical_arxiv(external_ids.get("ArXiv")) == _canonical_arxiv(arxiv):
        return True
    doi = source_ids.get("doi") or item.metadata.get("doi")
    if doi and str(external_ids.get("DOI") or "").lower() == str(doi).lower():
        return True
    semantic_scholar = source_ids.get("semantic_scholar") or item.metadata.get("semantic_scholar_paper_id")
    if semantic_scholar and str(payload.get("paperId") or "") == str(semantic_scholar):
        return True
    return _title_match(item.title, str(payload.get("title") or ""))


def _title_match(left: str, right: str) -> bool:
    left_norm = _normalize_title(left)
    right_norm = _normalize_title(right)
    if not left_norm or not right_norm:
        return False
    if left_norm == right_norm:
        return True
    return SequenceMatcher(None, left_norm, right_norm).ratio() >= TITLE_MATCH_THRESHOLD


def _cache_key(item: SignalItem) -> str:
    ids = _lookup_ids(item)
    if ids:
        return ids[0].lower()
    return f"title:{_normalize_title(item.title)}"


def _normalize_title(value: str) -> str:
    return NON_ALNUM_RE.sub(" ", str(value).lower()).strip()


def _canonical_arxiv(value: object) -> str:
    text = str(value or "").strip()
    if "/" in text and "arxiv.org" in text:
        text = text.rstrip("/").rsplit("/", 1)[-1]
    if text.lower().startswith("arxiv:"):
        text = text.split(":", 1)[1]
    return re.sub(r"v\d+$", "", text, flags=re.IGNORECASE)


def _author_names(authors: list[Any]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for author in authors:
        raw = author.get("name") if isinstance(author, dict) else author
        name = str(raw or "").strip()
        if name and name not in seen:
            names.append(name)
            seen.add(name)
    return names


def _parse_cached_at(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
