"""arXiv and OpenReview fetch stages for scholar mode."""

from __future__ import annotations

import re
from datetime import datetime, timezone
import math
from typing import Any

from defusedxml import ElementTree as ET
import httpx

from aurora.config import ScholarModeConfig
from aurora.modes.scholar.fields import expanded_arxiv_categories
from aurora.pipeline import StageContext


ARXIV_API_URL = "https://export.arxiv.org/api/query"
OPENREVIEW_API_URL = "https://api2.openreview.net/notes"
OPENREVIEW_BASE_URL = "https://openreview.net"
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}
CODE_URL_RE = re.compile(
    r"https?://(?:github|gitlab|bitbucket)\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[^\s<>()\]\}]+)?"
)
PROJECT_URL_RE = re.compile(r"https?://(?!github\.com)[^\s<>()\]\}]+")
VERSION_SUFFIX_RE = re.compile(r"v\d+$")
YEAR_RE = re.compile(r"(20\d{2})")


class ArxivFetchStage:
    """Fetch recent ML papers from arXiv Atom API."""

    name = "arxiv"

    def __init__(
        self,
        config: ScholarModeConfig,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self.http_client = http_client

    async def fetch(self, context: StageContext) -> list[dict[str, Any]]:
        if not self.config.sources.arxiv.enabled:
            return []
        if self.http_client is not None:
            return await self._fetch_with_client(self.http_client, context)
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            return await self._fetch_with_client(client, context)

    async def _fetch_with_client(
        self, client: httpx.AsyncClient, context: StageContext
    ) -> list[dict[str, Any]]:
        categories = expanded_arxiv_categories(self.config)
        max_results = self.config.sources.arxiv.max_results or self.config.max_candidates
        per_category_limit = max(1, math.ceil(max_results / max(1, len(categories))))
        records: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for category in categories:
            try:
                response = await client.get(
                    ARXIV_API_URL,
                    params={
                        "search_query": f"cat:{category}",
                        "start": "0",
                        "max_results": str(per_category_limit),
                        "sortBy": "submittedDate",
                        "sortOrder": "descending",
                    },
                    headers={"User-Agent": "Aurora-Scholar/0.1"},
                )
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                context.metadata.setdefault("scholar_source_failures", []).append(
                    {
                        "source": "arxiv",
                        "category": category,
                        "status_code": str(exc.response.status_code),
                        "error": str(exc),
                    }
                )
                continue
            for record in _parse_arxiv_feed(response.text, context):
                metadata = record.get("metadata", {}) if isinstance(record.get("metadata"), dict) else {}
                source_ids = metadata.get("source_ids", {}) if isinstance(metadata.get("source_ids"), dict) else {}
                canonical_id = str(source_ids.get("arxiv") or record.get("id") or "")
                if not canonical_id or canonical_id in seen_ids:
                    continue
                seen_ids.add(canonical_id)
                records.append(record)
                if len(records) >= max_results:
                    return records
        return records


class OpenReviewFetchStage:
    """Fetch public OpenReview submissions for configured venues."""

    name = "openreview"

    def __init__(
        self,
        config: ScholarModeConfig,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self.http_client = http_client

    async def fetch(self, context: StageContext) -> list[dict[str, Any]]:
        if not self.config.sources.openreview.enabled:
            return []
        if self.http_client is not None:
            return await self._fetch_with_client(self.http_client, context)
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            return await self._fetch_with_client(client, context)

    async def _fetch_with_client(
        self, client: httpx.AsyncClient, context: StageContext
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for venue_id in self.config.sources.openreview.venue_ids:
            try:
                response = await client.get(
                    OPENREVIEW_API_URL,
                    params={
                        "content.venueid": venue_id,
                        "details": "directReplies",
                        "limit": str(self.config.max_candidates),
                    },
                    headers={"User-Agent": "Aurora-Scholar/0.1"},
                )
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                context.metadata.setdefault("scholar_source_failures", []).append(
                    {
                        "source": "openreview",
                        "venue_id": venue_id,
                        "status_code": str(exc.response.status_code),
                        "error": str(exc),
                    }
                )
                continue
            payload = response.json()
            for note in payload.get("notes", []):
                if not isinstance(note, dict):
                    continue
                record = _parse_openreview_note(note, venue_id)
                if record is None or record["id"] in seen_ids:
                    continue
                if _is_before_since(record["published_at"], context):
                    continue
                seen_ids.add(record["id"])
                records.append(record)
        return records


def _parse_arxiv_feed(xml_text: str, context: StageContext) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_text)
    records: list[dict[str, Any]] = []
    for entry in root.findall("atom:entry", ATOM_NS):
        record = _parse_arxiv_entry(entry)
        if record is None:
            continue
        if _is_before_since(record["published_at"], context):
            continue
        records.append(record)
    return records


def _parse_arxiv_entry(entry: ET.Element) -> dict[str, Any] | None:
    entry_id = _child_text(entry, "id")
    versioned_id, canonical_id = _extract_arxiv_ids(entry_id)
    published_at = _parse_iso_datetime(_child_text(entry, "published"))
    title = _clean_text(_child_text(entry, "title"))
    abstract = _clean_text(_child_text(entry, "summary"))
    if not versioned_id or not canonical_id or published_at is None or not title or not abstract:
        return None

    updated_at = _parse_iso_datetime(_child_text(entry, "updated"))
    url = _extract_link(entry, rel="alternate") or f"https://arxiv.org/abs/{versioned_id}"
    pdf_url = _extract_pdf_url(entry) or f"https://arxiv.org/pdf/{versioned_id}"
    source_ids = {"arxiv": canonical_id, "arxiv_versioned": versioned_id}
    return {
        "id": f"arxiv:{canonical_id}",
        "source": "arxiv",
        "title": title,
        "url": url,
        "published_at": published_at,
        "updated_at": updated_at,
        "abstract": abstract,
        "metadata": {
            "authors": _extract_authors(entry),
            "pdf_url": pdf_url,
            "venue": None,
            "venue_year": published_at.year,
            "status": "preprint",
            "categories": _extract_categories(entry),
            "code_urls": CODE_URL_RE.findall(abstract),
            "project_urls": _project_urls(abstract),
            "source_ids": source_ids,
            "citation_count": None,
            "influential_citation_count": None,
            "semantic_scholar_paper_id": None,
        },
    }


def _parse_openreview_note(note: dict[str, Any], fallback_venue_id: str) -> dict[str, Any] | None:
    note_id = str(note.get("id") or "").strip()
    if not note_id:
        return None
    content = note.get("content") if isinstance(note.get("content"), dict) else {}
    title = _clean_text(_content_value(content, "title"))
    abstract = _clean_text(_content_value(content, "abstract"))
    if not title or not abstract:
        return None

    venue_id = _clean_text(_content_value(content, "venueid")) or fallback_venue_id
    venue_label = _clean_text(_content_value(content, "venue"))
    forum_id = str(note.get("forum") or note_id)
    published_at = _timestamp(note.get("cdate") or note.get("tcdate"))
    updated_at = _timestamp(note.get("mdate") or note.get("tmdate"))
    source_ids = {"openreview": note_id, "openreview_forum": forum_id}
    doi = _clean_text(_content_value(content, "doi"))
    if doi:
        source_ids["doi"] = doi

    return {
        "id": f"openreview:{note_id}",
        "source": "openreview",
        "title": title,
        "url": f"{OPENREVIEW_BASE_URL}/forum?id={forum_id}",
        "published_at": published_at,
        "updated_at": updated_at,
        "abstract": abstract,
        "metadata": {
            "authors": _authors(content),
            "pdf_url": _pdf_url(content, note_id),
            "venue": _venue_name(venue_id, venue_label),
            "venue_year": _venue_year(venue_id, venue_label),
            "status": _status(note, venue_id),
            "categories": [],
            "code_urls": _urls_for_keys(content, ("code", "code_url", "github", "repository")),
            "project_urls": _urls_for_keys(content, ("project", "project_page", "website")),
            "source_ids": source_ids,
            "citation_count": None,
            "influential_citation_count": None,
            "semantic_scholar_paper_id": None,
        },
    }


def _child_text(entry: ET.Element, child_name: str) -> str:
    child = entry.find(f"atom:{child_name}", ATOM_NS)
    return child.text or "" if child is not None else ""


def _extract_arxiv_ids(entry_id: str) -> tuple[str, str]:
    versioned = entry_id.split("/abs/", 1)[1].strip() if "/abs/" in entry_id else entry_id.rstrip("/").split("/")[-1]
    canonical = VERSION_SUFFIX_RE.sub("", versioned)
    return versioned, canonical


def _parse_iso_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _extract_authors(entry: ET.Element) -> list[str]:
    authors: list[str] = []
    for author in entry.findall("atom:author", ATOM_NS):
        name = author.find("atom:name", ATOM_NS)
        text = _clean_text(name.text if name is not None else "")
        if text:
            authors.append(text)
    return authors


def _extract_categories(entry: ET.Element) -> list[str]:
    seen: set[str] = set()
    categories: list[str] = []
    for category in entry.findall("atom:category", ATOM_NS):
        term = (category.attrib.get("term") or "").strip()
        if term and term not in seen:
            categories.append(term)
            seen.add(term)
    return categories


def _extract_link(entry: ET.Element, *, rel: str) -> str | None:
    for link in entry.findall("atom:link", ATOM_NS):
        if link.attrib.get("rel") == rel and link.attrib.get("href"):
            return link.attrib["href"]
    return None


def _extract_pdf_url(entry: ET.Element) -> str | None:
    for link in entry.findall("atom:link", ATOM_NS):
        if (link.attrib.get("title") or "").lower() == "pdf" and link.attrib.get("href"):
            return link.attrib["href"]
    return None


def _content_value(content: dict[str, Any], key: str) -> Any:
    value = content.get(key)
    if isinstance(value, dict) and "value" in value:
        return value["value"]
    return value


def _authors(content: dict[str, Any]) -> list[str]:
    raw = _content_value(content, "authors") or []
    if isinstance(raw, str):
        raw = [raw]
    return [_clean_text(author) for author in raw if _clean_text(author)]


def _pdf_url(content: dict[str, Any], note_id: str) -> str:
    value = _content_value(content, "pdf")
    if isinstance(value, str) and value.strip():
        cleaned = value.strip()
        if cleaned.startswith("http"):
            return cleaned
        if cleaned.startswith("/"):
            return f"{OPENREVIEW_BASE_URL}{cleaned}"
    return f"{OPENREVIEW_BASE_URL}/pdf?id={note_id}"


def _timestamp(value: Any) -> datetime:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)
    if numeric > 10_000_000_000:
        numeric /= 1000.0
    return datetime.fromtimestamp(numeric, tz=timezone.utc)


def _venue_name(venue_id: str, venue_label: str) -> str | None:
    haystack = f"{venue_id} {venue_label}".lower()
    if "iclr" in haystack:
        return "ICLR"
    if "icml" in haystack:
        return "ICML"
    if "neurips" in haystack or "nips" in haystack:
        return "NeurIPS"
    if "aistats" in haystack:
        return "AISTATS"
    return venue_id.split(".", 1)[0].split("/", 1)[0] or None


def _venue_year(venue_id: str, venue_label: str) -> int | None:
    match = YEAR_RE.search(f"{venue_id} {venue_label}")
    return int(match.group(1)) if match else None


def _status(note: dict[str, Any], venue_id: str) -> str:
    content = note.get("content") if isinstance(note.get("content"), dict) else {}
    text = " ".join(
        str(_content_value(content, key) or "")
        for key in ("venue", "venueid", "decision", "status", "paper_type", "presentation")
    ).lower()
    if "oral" in text:
        return "oral"
    if "spotlight" in text:
        return "spotlight"
    if "workshop" in text:
        return "workshop"
    if "accept" in text or "published" in text or "conference paper" in text:
        return "accepted"
    if "submitted" in text or "under review" in text or "submission" in text:
        return "submitted"
    return "submitted" if venue_id else "unknown"


def _urls_for_keys(content: dict[str, Any], keys: tuple[str, ...]) -> list[str]:
    urls: list[str] = []
    for key in keys:
        raw = _content_value(content, key)
        values = raw if isinstance(raw, list) else [raw]
        for value in values:
            if value:
                urls.extend(re.findall(r"https?://[^\s<>()\]\}]+", str(value)))
    return list(dict.fromkeys(urls))


def _project_urls(text: str) -> list[str]:
    return [url for url in PROJECT_URL_RE.findall(text) if "github.com" not in url]


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _is_before_since(value: datetime, context: StageContext) -> bool:
    return context.since is not None and value < context.since
