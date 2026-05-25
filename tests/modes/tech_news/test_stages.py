from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from aurora.config import TechNewsFiltersConfig, TechNewsScoringConfig
from aurora.modes.tech_news.render import TechNewsRenderer, TechNewsSummarizer
from aurora.modes.tech_news.scoring import TechNewsEnricher, TechNewsScorer
from aurora.modes.tech_news.stages import TechNewsDeduplicateStage, TechNewsNormalizeStage
from aurora.models import SignalItem
from aurora.pipeline import StageContext


def _context() -> StageContext:
    return StageContext(
        mode="tech_news",
        run_id="test",
        since=datetime(2026, 5, 25, tzinfo=timezone.utc),
        until=datetime(2026, 5, 26, tzinfo=timezone.utc),
    )


def test_normalize_converts_records_to_signal_items() -> None:
    raw = [
        {
            "id": "rss:feed:item",
            "source": "rss",
            "title": "AI News",
            "url": "https://example.com/ai",
            "published_at": datetime(2026, 5, 26, tzinfo=timezone.utc),
            "raw_content": "content",
            "metadata": {"feed_name": "Feed", "tags": ["AI"]},
        }
    ]

    items = asyncio.run(TechNewsNormalizeStage().normalize(raw, _context()))

    assert items == [
        SignalItem(
            id="rss:feed:item",
            type="news",
            title="AI News",
            url="https://example.com/ai",
            source="rss",
            published_at=datetime(2026, 5, 26, tzinfo=timezone.utc),
            raw_content="content",
            metadata={"feed_name": "Feed", "tags": ["AI"]},
            tags=["AI"],
        )
    ]


def test_deduplicate_collapses_canonical_urls_and_titles() -> None:
    items = [
        _item("news:1", "AI Breakthrough", "https://www.example.com/story/"),
        _item("news:2", "Different Title", "https://example.com/story"),
        _item("news:3", "AI Breakthrough", "https://other.example.com/story"),
    ]

    deduped = asyncio.run(TechNewsDeduplicateStage().deduplicate(items, _context()))

    assert [item.id for item in deduped] == ["news:1"]


def test_scoring_increases_with_engagement_recency_and_keywords() -> None:
    scorer = TechNewsScorer(
        TechNewsFiltersConfig(include_keywords=["ai", "agent"]),
        TechNewsScoringConfig(),
    )
    weak = _item(
        "news:weak",
        "Quiet Update",
        "https://example.com/weak",
        source="rss",
        published_at=datetime(2026, 5, 20, tzinfo=timezone.utc),
    )
    strong = _item(
        "news:strong",
        "AI Agent Launch",
        "https://example.com/strong",
        source="hackernews",
        published_at=datetime(2026, 5, 26, tzinfo=timezone.utc),
        metadata={"score": 500, "descendants": 80},
    )

    weak_score, strong_score = asyncio.run(scorer.score([weak, strong], _context()))

    assert strong_score.final_score > weak_score.final_score
    assert strong_score.tags == ["hackernews", "ai", "agent"]


def test_default_scoring_prioritizes_timely_high_engagement_news() -> None:
    scorer = TechNewsScorer(TechNewsFiltersConfig(), TechNewsScoringConfig())
    popular_timely = _item(
        "news:popular",
        "Database Outage Postmortem",
        "https://example.com/popular",
        source="hackernews",
        published_at=datetime(2026, 5, 26, tzinfo=timezone.utc),
        metadata={"score": 1200, "descendants": 300},
    )
    niche_keyword = _item(
        "news:niche",
        "AI Agent Library",
        "https://example.com/niche",
        source="rss",
        published_at=datetime(2026, 5, 20, tzinfo=timezone.utc),
    )

    popular_score, niche_score = asyncio.run(scorer.score([popular_timely, niche_keyword], _context()))

    assert popular_score.final_score > niche_score.final_score
    assert TechNewsScoringConfig().engagement_weight > TechNewsScoringConfig().topic_relevance_weight
    assert TechNewsScoringConfig().recency_weight > TechNewsScoringConfig().topic_relevance_weight


def test_enricher_applies_score_results_to_items() -> None:
    item = _item("news:1", "AI", "https://example.com/ai")
    scorer = TechNewsScorer(TechNewsFiltersConfig(include_keywords=["ai"]), TechNewsScoringConfig())
    score = asyncio.run(scorer.score([item], _context()))[0]

    enriched = asyncio.run(TechNewsEnricher().enrich([item], [score], _context()))

    assert enriched[0].final_score == score.final_score
    assert enriched[0].llm_score is None
    assert "score_breakdown" in enriched[0].metadata


def test_markdown_rendering_is_stable_and_score_ordered() -> None:
    low = _item("news:low", "Low", "https://example.com/low").model_copy(
        update={"final_score": 3.0}
    )
    high = _item("news:high", "High", "https://example.com/high").model_copy(
        update={"final_score": 8.0}
    )

    summary = asyncio.run(TechNewsSummarizer().summarize([low, high], _context()))
    rendered = asyncio.run(TechNewsRenderer().render(summary, [low, high], _context()))

    assert summary.index("High") < summary.index("Low")
    assert "Selected 2 tech news item(s)." in summary
    assert rendered.mode == "tech_news"
    assert rendered.markdown == summary


def _item(
    item_id: str,
    title: str,
    url: str,
    *,
    source: str = "rss",
    published_at: datetime | None = None,
    metadata: dict | None = None,
) -> SignalItem:
    return SignalItem(
        id=item_id,
        type="news",
        title=title,
        url=url,
        source=source,
        published_at=published_at or datetime(2026, 5, 26, tzinfo=timezone.utc),
        metadata=metadata or {},
    )
