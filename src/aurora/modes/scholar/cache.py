"""Snapshot cache for scholar fallback papers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from aurora.config import ScholarModeConfig
from aurora.models import SignalItem
from aurora.pipeline import StageContext
from aurora.storage.jsonl import read_jsonl, write_jsonl


CACHE_RELATIVE_PATH = Path("scholar") / "latest_enriched.jsonl"


def write_scholar_cache(items: list[SignalItem], context: StageContext) -> Path | None:
    """Persist last successful enriched scholar papers."""
    if not items or context.config is None:
        return None
    path = context.config.run.cache_dir / CACHE_RELATIVE_PATH
    return write_jsonl(path, items)


def load_scholar_cache(
    config: ScholarModeConfig,
    context: StageContext,
) -> list[SignalItem]:
    """Load recent cached scholar papers, returning an empty list on any cache issue."""
    if context.config is None:
        return []
    path = context.config.run.cache_dir / CACHE_RELATIVE_PATH
    if not path.exists() or _expired(path, config, context):
        return []
    try:
        rows = read_jsonl(path)
        items = [SignalItem.model_validate(row) for row in rows if isinstance(row, dict)]
    except Exception:
        return []
    return [_mark_cached(item) for item in items if item.type == "paper"]


def _expired(path: Path, config: ScholarModeConfig, context: StageContext) -> bool:
    now = context.until or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return modified < now - timedelta(hours=config.fallback_cache_ttl_hours)


def _mark_cached(item: SignalItem) -> SignalItem:
    metadata: dict[str, Any] = dict(item.metadata)
    metadata["cached_fallback"] = True
    if not item.why_it_matters:
        why_it_matters = "Cached scholar result from a recent successful run."
    else:
        why_it_matters = item.why_it_matters
    return item.model_copy(update={"metadata": metadata, "why_it_matters": why_it_matters})
