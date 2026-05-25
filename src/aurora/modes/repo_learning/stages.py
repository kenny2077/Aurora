"""Normalize, deduplicate, and deliver stages for repo_learning mode."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from aurora.modes.repo_learning.state import RepoLearningStateStore
from aurora.models import DeliveryResult, RenderedDigest, SignalItem
from aurora.pipeline import StageContext


class RepoLearningNormalizeStage:
    """Normalize GitHub repository records into SignalItem objects."""

    async def normalize(self, raw_items: Sequence[Any], context: StageContext) -> list[SignalItem]:
        items: list[SignalItem] = []
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            item = _normalize_repo(raw)
            if item is not None:
                items.append(item)
        return items


class RepoLearningDeduplicateStage:
    """Deduplicate repositories by GitHub identity."""

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
            kept[duplicate_index] = _merge_repos(kept[duplicate_index], item)
            for key in dedup_keys(kept[duplicate_index]):
                seen[key] = duplicate_index
        return kept


class RepoLearningDeliveryStage:
    """No-op delivery that records selected repositories as recently recommended."""

    def __init__(self, state_store: RepoLearningStateStore, downstream=None) -> None:
        self.state_store = state_store
        self.downstream = downstream

    async def deliver(
        self, rendered: RenderedDigest, context: StageContext
    ) -> list[DeliveryResult]:
        repo_ids = [
            str(repo_id)
            for repo_id in rendered.metadata.get("recommended_repo_ids", [])
            if str(repo_id).strip()
        ]
        self.state_store.mark_recommended(repo_ids, context.until or datetime.now(timezone.utc))
        state_result = DeliveryResult(
            channel="repo_learning_state",
            metadata={"recommended_count": len(repo_ids)},
        )
        if self.downstream is None:
            return [state_result]
        return [
            state_result,
            *(await self.downstream.deliver(rendered, context)),
        ]


def dedup_keys(item: SignalItem) -> list[str]:
    metadata = item.metadata
    candidates = [
        ("github_id", metadata.get("github_id")),
        ("node_id", metadata.get("node_id")),
        ("full_name", _normalize_full_name(str(metadata.get("full_name") or ""))),
    ]
    return [f"{kind}:{str(value).lower().strip()}" for kind, value in candidates if value]


def _normalize_repo(raw: dict[str, Any]) -> SignalItem | None:
    full_name = str(raw.get("full_name") or "").strip()
    if "/" not in full_name:
        return None
    owner, name = full_name.split("/", 1)
    updated_at = _parse_github_datetime(
        raw.get("pushed_at") or raw.get("updated_at") or raw.get("created_at")
    )
    if updated_at is None:
        return None
    description = str(raw.get("description") or "").strip()
    topics = [str(topic).strip() for topic in raw.get("topics") or [] if str(topic).strip()]
    language = str(raw.get("language") or "").strip()
    metadata = {
        "github_id": raw.get("id"),
        "node_id": raw.get("node_id"),
        "owner": owner,
        "name": name,
        "full_name": full_name,
        "description": description,
        "stars": _int(raw.get("stargazers_count")),
        "forks": _int(raw.get("forks_count")),
        "watchers": _int(raw.get("watchers_count")),
        "open_issues": _int(raw.get("open_issues_count")),
        "language": language or None,
        "topics": topics,
        "default_branch": str(raw.get("default_branch") or "main"),
        "homepage": str(raw.get("homepage") or "").strip() or None,
        "license": _license(raw.get("license")),
        "source_domains": [str(raw.get("aurora_source_domain") or "github_search")],
        "source_queries": [str(raw.get("aurora_search_query") or "").strip()],
        "created_at": _parse_github_datetime(raw.get("created_at")),
        "pushed_at": updated_at,
    }
    tags = list(dict.fromkeys([*(topics[:8]), *([language] if language else [])]))
    return SignalItem(
        id=f"repo:{full_name}",
        type="repo",
        title=full_name,
        url=str(raw.get("html_url") or f"https://github.com/{full_name}"),
        source="github_search",
        updated_at=updated_at,
        raw_content=description,
        metadata=metadata,
        tags=tags,
    )


def _merge_repos(first: SignalItem, second: SignalItem) -> SignalItem:
    preferred, other = _prefer_repo(first, second)
    metadata = dict(preferred.metadata)
    for key in ("source_domains", "source_queries", "topics"):
        metadata[key] = list(
            dict.fromkeys([*(metadata.get(key) or []), *(other.metadata.get(key) or [])])
        )
    return preferred.model_copy(
        update={
            "metadata": metadata,
            "tags": list(dict.fromkeys([*preferred.tags, *other.tags])),
        }
    )


def _prefer_repo(first: SignalItem, second: SignalItem) -> tuple[SignalItem, SignalItem]:
    first_stars = int(first.metadata.get("stars") or 0)
    second_stars = int(second.metadata.get("stars") or 0)
    if second_stars > first_stars:
        return second, first
    return first, second


def _normalize_full_name(value: str) -> str:
    return re.sub(r"\s+", "", value.strip().lower())


def _parse_github_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _license(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    return str(value.get("spdx_id") or value.get("name") or "").strip() or None
