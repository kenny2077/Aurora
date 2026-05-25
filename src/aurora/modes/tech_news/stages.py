"""Normalize, deduplicate, and deliver stages for tech_news mode."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from aurora.models import DeliveryResult, ScoreResult, SignalItem
from aurora.pipeline import StageContext


class TechNewsNormalizeStage:
    """Normalize RSS/Hacker News records into SignalItem objects."""

    async def normalize(self, raw_items: Sequence[Any], context: StageContext) -> list[SignalItem]:
        items: list[SignalItem] = []
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            items.append(
                SignalItem(
                    id=str(raw["id"]),
                    type="news",
                    title=str(raw["title"]),
                    url=str(raw["url"]),
                    source=str(raw["source"]),
                    published_at=raw.get("published_at"),
                    updated_at=raw.get("updated_at"),
                    raw_content=str(raw.get("raw_content") or ""),
                    metadata=dict(raw.get("metadata") or {}),
                    tags=list((raw.get("metadata") or {}).get("tags") or []),
                )
            )
        return items


class TechNewsDeduplicateStage:
    """Deduplicate news by canonical URL first, then normalized title."""

    async def deduplicate(
        self, items: Sequence[SignalItem], context: StageContext
    ) -> list[SignalItem]:
        kept: list[SignalItem] = []
        url_indexes: dict[str, int] = {}
        title_indexes: dict[str, int] = {}

        for item in items:
            url_key = canonical_url(str(item.url))
            title_key = normalize_title(item.title)
            duplicate_index = url_indexes.get(url_key)
            if duplicate_index is None:
                duplicate_index = title_indexes.get(title_key)

            if duplicate_index is None:
                kept.append(item)
                index = len(kept) - 1
                url_indexes[url_key] = index
                title_indexes[title_key] = index
                continue

            current = kept[duplicate_index]
            preferred = _prefer_richer_item(current, item)
            kept[duplicate_index] = preferred
            url_indexes[canonical_url(str(preferred.url))] = duplicate_index
            title_indexes[normalize_title(preferred.title)] = duplicate_index

        return kept


class NoopDeliveryStage:
    """No-op delivery used until real delivery channels are implemented."""

    async def deliver(
        self, rendered, context: StageContext
    ) -> list[DeliveryResult]:
        return [DeliveryResult(channel="dry_run")]


def canonical_url(url: str) -> str:
    parsed = urlsplit(url)
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme.lower(), host, path, parsed.query, ""))


def normalize_title(title: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", title.lower())).strip()


def _prefer_richer_item(first: SignalItem, second: SignalItem) -> SignalItem:
    first_richness = len(first.raw_content) + len(first.metadata)
    second_richness = len(second.raw_content) + len(second.metadata)
    return second if second_richness > first_richness else first

