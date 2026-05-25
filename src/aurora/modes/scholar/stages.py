"""Normalize, deduplicate, and delivery stages for scholar mode."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from aurora.models import DeliveryResult, SignalItem
from aurora.pipeline import StageContext


class ScholarNormalizeStage:
    """Normalize paper records into SignalItem objects."""

    async def normalize(self, raw_items: Sequence[Any], context: StageContext) -> list[SignalItem]:
        items: list[SignalItem] = []
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            metadata = dict(raw.get("metadata") or {})
            items.append(
                SignalItem(
                    id=str(raw["id"]),
                    type="paper",
                    title=str(raw["title"]),
                    url=str(raw["url"]),
                    source=str(raw["source"]),
                    published_at=raw.get("published_at"),
                    updated_at=raw.get("updated_at"),
                    raw_content=str(raw.get("abstract") or ""),
                    metadata=metadata,
                    tags=list(metadata.get("categories") or []),
                )
            )
        return items


class ScholarDeduplicateStage:
    """Deduplicate papers by stable academic identity."""

    async def deduplicate(
        self, items: Sequence[SignalItem], context: StageContext
    ) -> list[SignalItem]:
        kept: list[SignalItem] = []
        seen: dict[str, int] = {}
        for item in items:
            keys = dedup_keys(item)
            duplicate_index = next((seen[key] for key in keys if key in seen), None)
            if duplicate_index is None:
                kept.append(item)
                index = len(kept) - 1
                for key in keys:
                    seen[key] = index
                continue
            kept[duplicate_index] = _merge_items(kept[duplicate_index], item)
            for key in dedup_keys(kept[duplicate_index]):
                seen[key] = duplicate_index
        return kept


class NoopDeliveryStage:
    """No-op delivery used until real delivery channels are implemented."""

    async def deliver(self, rendered, context: StageContext) -> list[DeliveryResult]:
        return [DeliveryResult(channel="dry_run")]


def dedup_keys(item: SignalItem) -> list[str]:
    metadata = item.metadata
    source_ids = metadata.get("source_ids") if isinstance(metadata.get("source_ids"), dict) else {}
    candidates = [
        ("doi", source_ids.get("doi") or metadata.get("doi")),
        ("arxiv", source_ids.get("arxiv")),
        ("openreview_forum", source_ids.get("openreview_forum")),
        ("semantic_scholar", source_ids.get("semantic_scholar") or metadata.get("semantic_scholar_paper_id")),
        ("title", normalize_title(item.title)),
    ]
    return [f"{kind}:{str(value).lower().strip()}" for kind, value in candidates if value]


def normalize_title(title: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", title.lower())).strip()


def _merge_items(first: SignalItem, second: SignalItem) -> SignalItem:
    metadata = dict(first.metadata)
    source_ids = dict(metadata.get("source_ids") or {})
    source_ids.update(second.metadata.get("source_ids") or {})
    metadata["source_ids"] = source_ids
    for key in ("authors", "categories", "code_urls", "project_urls"):
        metadata[key] = list(dict.fromkeys([*(metadata.get(key) or []), *(second.metadata.get(key) or [])]))
    return first.model_copy(update={"metadata": metadata})

