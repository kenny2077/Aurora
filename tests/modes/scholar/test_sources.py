from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx

from aurora.config import ScholarModeConfig, ScholarSourcesConfig, ArxivSourceConfig, OpenReviewSourceConfig
from aurora.modes.scholar.sources import ArxivFetchStage, OpenReviewFetchStage
from aurora.pipeline import StageContext


def test_arxiv_fetch_parses_atom_and_skips_old_papers() -> None:
    since = datetime(2026, 5, 25, tzinfo=timezone.utc)
    feed = """
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <id>https://arxiv.org/abs/2605.12345v2</id>
        <published>2026-05-26T00:00:00Z</published>
        <updated>2026-05-26T01:00:00Z</updated>
        <title> Efficient LLM Agents </title>
        <summary> We introduce an evaluation benchmark with code at https://github.com/org/repo and project page https://agents.example.com. </summary>
        <author><name>Ada Lovelace</name></author>
        <author><name>Alan Turing</name></author>
        <category term="cs.AI" />
        <link rel="alternate" href="https://arxiv.org/abs/2605.12345v2" />
        <link title="pdf" href="https://arxiv.org/pdf/2605.12345v2" />
      </entry>
      <entry>
        <id>https://arxiv.org/abs/2001.00001v1</id>
        <published>2020-01-01T00:00:00Z</published>
        <title>Old Paper</title>
        <summary>Old abstract.</summary>
        <category term="cs.AI" />
      </entry>
    </feed>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).startswith("https://export.arxiv.org/api/query")
        return httpx.Response(200, text=feed)

    async def exercise() -> list[dict]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await ArxivFetchStage(
                ScholarModeConfig(sources=ScholarSourcesConfig(arxiv=ArxivSourceConfig(max_results=2))),
                http_client=client,
            ).fetch(StageContext(mode="scholar", run_id="test", since=since))

    records = asyncio.run(exercise())

    assert len(records) == 1
    assert records[0]["id"] == "arxiv:2605.12345"
    assert records[0]["metadata"]["authors"] == ["Ada Lovelace", "Alan Turing"]
    assert records[0]["metadata"]["categories"] == ["cs.AI"]
    assert records[0]["metadata"]["source_ids"]["arxiv"] == "2605.12345"
    assert records[0]["metadata"]["pdf_url"] == "https://arxiv.org/pdf/2605.12345v2"
    assert records[0]["metadata"]["code_urls"] == ["https://github.com/org/repo"]


def test_openreview_fetch_parses_notes_and_skips_invalid() -> None:
    cdate = int(datetime(2026, 5, 26, tzinfo=timezone.utc).timestamp() * 1000)
    payload = {
        "notes": [
            {
                "id": "abc123",
                "forum": "forum123",
                "cdate": cdate,
                "mdate": cdate,
                "content": {
                    "title": {"value": "Interpretable Reasoning"},
                    "abstract": {"value": "We evaluate a method with baselines and ablations."},
                    "authors": {"value": ["Grace Hopper"]},
                    "venueid": {"value": "ICLR.cc/2026/Conference"},
                    "venue": {"value": "ICLR 2026 Conference Paper"},
                    "pdf": {"value": "/pdf?id=abc123"},
                    "github": {"value": "https://github.com/org/paper"},
                    "project": {"value": "https://paper.example.com"},
                    "doi": {"value": "10.1234/example"},
                },
            },
            {"id": "missing-title", "content": {"abstract": {"value": "No title"}}},
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).startswith("https://api2.openreview.net/notes")
        return httpx.Response(200, json=payload)

    async def exercise() -> list[dict]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await OpenReviewFetchStage(
                ScholarModeConfig(
                    sources=ScholarSourcesConfig(
                        openreview=OpenReviewSourceConfig(venue_ids=["ICLR.cc/2026/Conference"])
                    )
                ),
                http_client=client,
            ).fetch(StageContext(mode="scholar", run_id="test", since=datetime(2026, 1, 1, tzinfo=timezone.utc)))

    records = asyncio.run(exercise())

    assert len(records) == 1
    assert records[0]["id"] == "openreview:abc123"
    assert records[0]["metadata"]["venue"] == "ICLR"
    assert records[0]["metadata"]["venue_year"] == 2026
    assert records[0]["metadata"]["status"] == "accepted"
    assert records[0]["metadata"]["source_ids"]["doi"] == "10.1234/example"
    assert records[0]["metadata"]["pdf_url"] == "https://openreview.net/pdf?id=abc123"

