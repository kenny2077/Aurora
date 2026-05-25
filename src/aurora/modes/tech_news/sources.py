"""RSS and Hacker News fetch stages for tech_news mode."""

from __future__ import annotations

import asyncio
import calendar
import hashlib
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import feedparser
import httpx

from aurora.config import HackerNewsSourceConfig, RSSSourceConfig
from aurora.pipeline import StageContext


class HackerNewsFetchStage:
    """Fetch Hacker News top stories from the public Firebase API."""

    name = "hackernews"

    def __init__(
        self,
        config: HackerNewsSourceConfig,
        *,
        http_client: httpx.AsyncClient | None = None,
        base_url: str = "https://hacker-news.firebaseio.com/v0",
    ) -> None:
        self.config = config
        self.http_client = http_client
        self.base_url = base_url.rstrip("/")

    async def fetch(self, context: StageContext) -> list[dict[str, Any]]:
        if not self.config.enabled:
            return []
        if self.http_client is not None:
            return await self._fetch_with_client(self.http_client, context)
        async with httpx.AsyncClient(timeout=20.0) as client:
            return await self._fetch_with_client(client, context)

    async def _fetch_with_client(
        self, client: httpx.AsyncClient, context: StageContext
    ) -> list[dict[str, Any]]:
        response = await client.get(f"{self.base_url}/topstories.json")
        response.raise_for_status()
        story_ids = list(response.json() or [])[: self.config.fetch_top_stories]
        stories = await asyncio.gather(
            *(self._fetch_item(client, story_id) for story_id in story_ids),
            return_exceptions=True,
        )

        records: list[dict[str, Any]] = []
        for story in stories:
            if not isinstance(story, dict):
                continue
            if story.get("type", "story") != "story":
                continue
            if int(story.get("score") or 0) < self.config.min_score:
                continue
            published_at = _timestamp_to_datetime(story.get("time"))
            if published_at is None or _is_before_since(published_at, context):
                continue
            comments = await self._fetch_comments(client, story.get("kids", []))
            records.append(_build_hackernews_record(story, comments, published_at))
        return records

    async def _fetch_item(self, client: httpx.AsyncClient, item_id: int) -> dict[str, Any] | None:
        try:
            response = await client.get(f"{self.base_url}/item/{item_id}.json")
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError:
            return None

    async def _fetch_comments(
        self, client: httpx.AsyncClient, comment_ids: list[int]
    ) -> list[dict[str, Any]]:
        if self.config.top_comments_limit == 0:
            return []
        selected_ids = comment_ids[: self.config.top_comments_limit]
        comments = await asyncio.gather(
            *(self._fetch_item(client, comment_id) for comment_id in selected_ids),
            return_exceptions=True,
        )
        return [
            comment
            for comment in comments
            if isinstance(comment, dict)
            and comment.get("text")
            and not comment.get("deleted")
            and not comment.get("dead")
        ]


class RSSFetchStage:
    """Fetch configured RSS/Atom feeds."""

    name = "rss"

    def __init__(
        self,
        sources: list[RSSSourceConfig],
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.sources = sources
        self.http_client = http_client

    async def fetch(self, context: StageContext) -> list[dict[str, Any]]:
        if not self.sources:
            return []
        if self.http_client is not None:
            return await self._fetch_with_client(self.http_client, context)
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            return await self._fetch_with_client(client, context)

    async def _fetch_with_client(
        self, client: httpx.AsyncClient, context: StageContext
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for source in self.sources:
            if not source.enabled:
                continue
            try:
                response = await client.get(str(source.url), follow_redirects=True)
                response.raise_for_status()
                feed = feedparser.parse(response.text)
            except Exception:
                continue
            for entry in feed.entries:
                published_at = _parse_feed_date(entry)
                if published_at is None or _is_before_since(published_at, context):
                    continue
                records.append(_build_rss_record(source, entry, published_at))
        return records


def _build_hackernews_record(
    story: dict[str, Any], comments: list[dict[str, Any]], published_at: datetime
) -> dict[str, Any]:
    story_id = int(story["id"])
    discussion_url = f"https://news.ycombinator.com/item?id={story_id}"
    raw_content_parts: list[str] = []
    if story.get("text"):
        raw_content_parts.append(_strip_html(str(story["text"])))
    for comment in comments:
        text = _strip_html(str(comment.get("text", "")))
        if len(text) > 500:
            text = text[:497] + "..."
        raw_content_parts.append(f"[{comment.get('by', 'anon')}]: {text}")

    return {
        "id": f"hackernews:story:{story_id}",
        "source": "hackernews",
        "title": str(story.get("title") or "Untitled"),
        "url": str(story.get("url") or discussion_url),
        "published_at": published_at,
        "raw_content": "\n\n".join(raw_content_parts),
        "metadata": {
            "author": story.get("by", "unknown"),
            "score": int(story.get("score") or 0),
            "descendants": int(story.get("descendants") or 0),
            "discussion_url": discussion_url,
            "comment_count": len(comments),
        },
    }


def _build_rss_record(
    source: RSSSourceConfig, entry: dict[str, Any], published_at: datetime
) -> dict[str, Any]:
    entry_id = str(entry.get("id") or entry.get("link") or entry.get("title") or "")
    entry_hash = hashlib.sha256(entry_id.encode("utf-8")).hexdigest()[:16]
    feed_id = hashlib.sha256(str(source.url).encode("utf-8")).hexdigest()[:10]
    tags = [
        str(getattr(tag, "term", "") or tag.get("term", "")).strip()
        for tag in entry.get("tags", [])
    ]
    return {
        "id": f"rss:{feed_id}:{entry_hash}",
        "source": "rss",
        "title": str(entry.get("title") or "Untitled"),
        "url": str(entry.get("link") or source.url),
        "published_at": published_at,
        "raw_content": _extract_rss_content(entry),
        "metadata": {
            "feed_name": source.name,
            "category": source.category,
            "tags": [tag for tag in tags if tag],
        },
    }


def _parse_feed_date(entry: dict[str, Any]) -> datetime | None:
    for field in ("published", "updated", "created"):
        parsed_field = f"{field}_parsed"
        if entry.get(parsed_field):
            return datetime.fromtimestamp(calendar.timegm(entry[parsed_field]), tz=timezone.utc)
        if entry.get(field):
            try:
                value = parsedate_to_datetime(str(entry[field]))
                return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError, OverflowError):
                continue
    return None


def _extract_rss_content(entry: dict[str, Any]) -> str:
    if entry.get("summary"):
        return str(entry.summary)
    if entry.get("description"):
        return str(entry.description)
    content = entry.get("content")
    if content:
        return str(content[0].get("value", ""))
    return ""


def _timestamp_to_datetime(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def _is_before_since(value: datetime, context: StageContext) -> bool:
    return context.since is not None and value < context.since


def _strip_html(value: str) -> str:
    return re.sub(r"<[^>]+>", " ", value).strip()

