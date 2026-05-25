from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx

from aurora.config import HackerNewsSourceConfig, RSSSourceConfig
from aurora.modes.tech_news.sources import HackerNewsFetchStage, RSSFetchStage
from aurora.pipeline import StageContext


def test_hackernews_fetch_filters_score_and_time_window() -> None:
    since = datetime(2026, 5, 25, 0, 0, tzinfo=timezone.utc)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v0/topstories.json":
            return httpx.Response(200, json=[1, 2, 3])
        if request.url.path == "/v0/item/1.json":
            return httpx.Response(
                200,
                json={
                    "id": 1,
                    "type": "story",
                    "title": "AI Agents",
                    "url": "https://example.com/ai-agents",
                    "score": 250,
                    "descendants": 12,
                    "by": "alice",
                    "time": 1780012800,
                    "kids": [10],
                },
            )
        if request.url.path == "/v0/item/2.json":
            return httpx.Response(200, json={"id": 2, "score": 5, "time": 1780012800})
        if request.url.path == "/v0/item/3.json":
            return httpx.Response(200, json={"id": 3, "score": 300, "time": 1700000000})
        if request.url.path == "/v0/item/10.json":
            return httpx.Response(200, json={"id": 10, "text": "<p>Great</p>", "by": "bob"})
        raise AssertionError(f"unexpected path {request.url.path}")

    async def exercise() -> list[dict]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await HackerNewsFetchStage(
                HackerNewsSourceConfig(min_score=100), http_client=client
            ).fetch(StageContext(mode="tech_news", run_id="test", since=since))

    records = asyncio.run(exercise())

    assert len(records) == 1
    assert records[0]["id"] == "hackernews:story:1"
    assert records[0]["metadata"]["score"] == 250
    assert records[0]["metadata"]["comment_count"] == 1
    assert "[bob]: Great" in records[0]["raw_content"]


def test_rss_fetch_parses_dates_skips_old_entries_and_tolerates_failures() -> None:
    since = datetime(2026, 5, 25, 0, 0, tzinfo=timezone.utc)
    feed = """
    <rss version="2.0">
      <channel>
        <title>Feed</title>
        <item>
          <title>New AI Tool</title>
          <link>https://example.com/new</link>
          <guid>new</guid>
          <pubDate>Tue, 26 May 2026 00:00:00 GMT</pubDate>
          <description>Useful for LLM developers.</description>
          <category>AI</category>
        </item>
        <item>
          <title>Old AI Tool</title>
          <link>https://example.com/old</link>
          <guid>old</guid>
          <pubDate>Tue, 01 Jan 2020 00:00:00 GMT</pubDate>
        </item>
      </channel>
    </rss>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://example.com/feed.xml":
            return httpx.Response(200, text=feed)
        if str(request.url) == "https://example.com/bad.xml":
            return httpx.Response(500)
        raise AssertionError(f"unexpected url {request.url}")

    async def exercise() -> list[dict]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await RSSFetchStage(
                [
                    RSSSourceConfig(name="Good", url="https://example.com/feed.xml"),
                    RSSSourceConfig(name="Bad", url="https://example.com/bad.xml"),
                ],
                http_client=client,
            ).fetch(StageContext(mode="tech_news", run_id="test", since=since))

    records = asyncio.run(exercise())

    assert len(records) == 1
    assert records[0]["title"] == "New AI Tool"
    assert records[0]["metadata"]["feed_name"] == "Good"
    assert records[0]["metadata"]["tags"] == ["AI"]

