from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from aurora.ai.ranker import LLMRanker
from aurora.config import AIConfig, AuroraConfig, FinalScoreWeights
from aurora.config import TechNewsFiltersConfig, TechNewsScoringConfig
from aurora.modes.tech_news.pipeline import build_tech_news_pipeline
from aurora.modes.tech_news.prompts import TECH_NEWS_ANALYSIS_SYSTEM
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


def test_deduplicate_collapses_hn_rss_reddit_and_github_release_overlap() -> None:
    items = [
        _item("hackernews:story:1", "Agent Release", "https://example.com/release/", source="hackernews"),
        _item("rss:feed:1", "Different", "https://www.example.com/release", source="rss"),
        _item("reddit:ml:1", "Agent Release", "https://reddit.com/r/MachineLearning/comments/1", source="reddit"),
        _item(
            "github_release:org/repo:1",
            "org/repo Agent Release",
            "https://github.com/org/repo/releases/tag/v1",
            source="github_releases",
            raw_content="longer release notes with practical agent tooling details",
        ),
    ]

    deduped = asyncio.run(TechNewsDeduplicateStage().deduplicate(items, _context()))

    assert [item.id for item in deduped] == [
        "hackernews:story:1",
        "github_release:org/repo:1",
    ]


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


def test_scoring_rewards_authoritative_rss_and_avoids_short_keyword_false_matches() -> None:
    scorer = TechNewsScorer(
        TechNewsFiltersConfig(include_keywords=["ai"]),
        TechNewsScoringConfig(),
    )
    authoritative = _item(
        "news:openai",
        "OpenAI releases an AI inference benchmark",
        "https://example.com/openai",
        source="rss",
        metadata={"feed_name": "OpenAI News"},
        raw_content="The release includes a benchmark, developer tooling, and production guidance.",
    )
    generic = _item(
        "news:generic",
        "Maintainers explain their chain of command",
        "https://example.com/generic",
        source="rss",
        metadata={"feed_name": "Generic Feed"},
        raw_content="This update is mainly an internal maintainer note.",
    )

    authoritative_score, generic_score = asyncio.run(
        scorer.score([authoritative, generic], _context())
    )

    assert authoritative_score.final_score > generic_score.final_score
    assert authoritative_score.tags == ["rss", "ai"]
    assert generic_score.tags == ["rss"]


def test_scoring_penalizes_flaky_source_quality() -> None:
    item = _item(
        "news:rss",
        "AI Agent Release",
        "https://example.com/story",
        source="rss",
        metadata={"feed_name": "OpenAI News"},
        raw_content="A benchmark and developer tool release for production agent systems.",
    )
    scorer = TechNewsScorer(TechNewsFiltersConfig(), TechNewsScoringConfig())

    healthy = asyncio.run(scorer.score([item], _context()))[0]
    flaky_context = _context()
    flaky_context.metadata["source_quality"] = {
        "tech_news:rss": {"quality_score": 2.0},
    }
    flaky = asyncio.run(scorer.score([item], flaky_context))[0]

    assert flaky.final_score < healthy.final_score
    assert flaky.score_breakdown["source_health"] == 2.0


def test_enricher_applies_score_results_to_items() -> None:
    item = _item("news:1", "AI", "https://example.com/ai")
    scorer = TechNewsScorer(TechNewsFiltersConfig(include_keywords=["ai"]), TechNewsScoringConfig())
    score = asyncio.run(scorer.score([item], _context()))[0]

    enriched = asyncio.run(TechNewsEnricher().enrich([item], [score], _context()))

    assert enriched[0].final_score == score.final_score
    assert enriched[0].llm_score is None
    assert "score_breakdown" in enriched[0].metadata


def test_enricher_generates_polished_hackernews_learning_notes() -> None:
    item = _item(
        "news:hn",
        "Can the stockmarket swallow Anthropic, SpaceX and OpenAI?",
        "https://example.com/story",
        source="hackernews",
        metadata={
            "score": 1240,
            "descendants": 314,
            "discussion_url": "https://news.ycombinator.com/item?id=1",
        },
        raw_content=(
            "https:&#x2F;&#x2F;archive.ph&#x2F;nKEVw [augstein]: For SpaceX yes "
            "because the rules changed."
        ),
    )
    score = asyncio.run(
        TechNewsScorer(TechNewsFiltersConfig(), TechNewsScoringConfig()).score([item], _context())
    )[0]

    enriched = asyncio.run(TechNewsEnricher().enrich([item], [score], _context()))[0]

    assert "Hacker News" in enriched.why_it_matters
    assert "1240" in enriched.why_it_matters
    assert enriched.learning_value
    assert enriched.action_items
    for value in (enriched.why_it_matters, enriched.learning_value):
        assert "&#x" not in value
        assert "https:" not in value
        assert "[augstein]:" not in value


def test_tech_news_prompt_does_not_request_credibility_prediction() -> None:
    assert "source_credibility" not in TECH_NEWS_ANALYSIS_SYSTEM
    assert "Likely true" not in TECH_NEWS_ANALYSIS_SYSTEM
    assert "Unverified" not in TECH_NEWS_ANALYSIS_SYSTEM


def test_enricher_ignores_llm_source_credibility_when_returned() -> None:
    item = _item(
        "news:llm",
        "OpenAI ships a new model",
        "https://example.com/model",
        source="rss",
        metadata={"feed_name": "OpenAI Blog"},
    )
    score = asyncio.run(
        TechNewsScorer(TechNewsFiltersConfig(), TechNewsScoringConfig()).score([item], _context())
    )[0]
    ranker = LLMRanker(
        AIConfig(api_key_env="AURORA_TEST_KEY"),
        weights=FinalScoreWeights(deterministic=0.5, llm=0.5),
        client=_CredibilityClient(),
    )

    enriched = asyncio.run(TechNewsEnricher(ranker).enrich([item], [score], _context()))[0]

    assert enriched.summary == "A concise LLM summary."
    assert "source_credibility" not in enriched.metadata


def test_enricher_limits_llm_analysis_to_top_ranked_news() -> None:
    context = _context()
    low = _item("news:low", "Low relevance update", "https://example.com/low", source="rss")
    high = _item(
        "news:high",
        "AI Agent Launch",
        "https://example.com/high",
        source="hackernews",
        metadata={"score": 1000, "descendants": 120},
    )
    scores = asyncio.run(
        TechNewsScorer(TechNewsFiltersConfig(), TechNewsScoringConfig()).score([low, high], context)
    )
    ranker = _RecordingRanker()

    enriched = asyncio.run(
        TechNewsEnricher(ranker, llm_analysis_top_n=1).enrich([low, high], scores, context)
    )

    assert [item.id for item in enriched] == ["news:low", "news:high"]
    assert ranker.item_ids == ["news:high"]
    assert context.metadata["llm_analysis_candidate_pool_count"] == 2


def test_tech_news_pipeline_adds_optional_source_packs_only_when_enabled() -> None:
    default_pipeline = build_tech_news_pipeline(AuroraConfig())
    expanded_pipeline = build_tech_news_pipeline(
        AuroraConfig(
            modes={
                "tech_news": {
                    "sources": {
                        "curated_rss_groups": ["ai_labs"],
                        "reddit": {"enabled": True, "subreddits": ["MachineLearning"]},
                        "github_releases": {
                            "enabled": True,
                            "repositories": ["openai/openai-python"],
                        },
                    }
                }
            }
        )
    )

    assert [stage.name for stage in default_pipeline.fetch_stages] == ["hackernews", "rss"]
    assert [stage.name for stage in expanded_pipeline.fetch_stages] == [
        "hackernews",
        "rss",
        "reddit",
        "github_releases",
    ]


def test_enricher_generates_clean_rss_learning_notes() -> None:
    item = _item(
        "news:rss",
        "Nvidia releases a compact AI workstation",
        "https://example.com/nvidia",
        source="rss",
        metadata={"feed_name": "Example Feed", "category": "AI infrastructure", "tags": ["GPU"]},
        raw_content="<p>Nvidia&#x27;s new workstation targets local inference &amp; prototyping.</p>",
    )
    score = asyncio.run(
        TechNewsScorer(TechNewsFiltersConfig(), TechNewsScoringConfig()).score([item], _context())
    )[0]

    enriched = asyncio.run(TechNewsEnricher().enrich([item], [score], _context()))[0]

    assert enriched.why_it_matters.startswith("The update describes")
    assert "local inference" in enriched.learning_value
    for value in (enriched.why_it_matters, enriched.learning_value):
        assert "<p>" not in value
        assert "&#x" not in value
        assert "&amp;" not in value
        assert "flagged this" not in value


def test_enricher_generates_mature_rss_fallback_summary() -> None:
    item = _item(
        "news:rss-polish",
        "Evaluate AI agents systematically with Agent-EvalKit",
        "https://example.com/agent-evalkit",
        source="rss",
        metadata={
            "feed_name": "AWS Machine Learning Blog",
            "category": "Artificial Intelligence",
            "tags": ["Amazon Bedrock", "Strands Agents"],
        },
        raw_content=(
            "<p>Agent-EvalKit helps teams evaluate multi-step agents with repeatable "
            "benchmarks and human review workflows.</p>"
        ),
    )
    score = asyncio.run(
        TechNewsScorer(TechNewsFiltersConfig(), TechNewsScoringConfig()).score([item], _context())
    )[0]

    enriched = asyncio.run(TechNewsEnricher().enrich([item], [score], _context()))[0]

    assert enriched.why_it_matters.startswith("The update describes")
    assert "repeatable benchmarks" in enriched.why_it_matters
    assert "covers" not in enriched.why_it_matters
    assert "flagged this" not in enriched.why_it_matters
    assert "story as timely" not in enriched.why_it_matters


def test_github_release_uses_public_source_label_and_release_summary() -> None:
    item = _item(
        "github_release:vllm:v023",
        "vllm-project/vllm v0.23.0",
        "https://github.com/vllm-project/vllm/releases/tag/v0.23.0",
        source="github_releases",
        raw_content=(
            "Release v0.23.0 adds faster inference paths, benchmark updates, "
            "and compatibility fixes for production serving."
        ),
    )
    score = asyncio.run(
        TechNewsScorer(TechNewsFiltersConfig(), TechNewsScoringConfig()).score([item], _context())
    )[0]

    enriched = asyncio.run(TechNewsEnricher().enrich([item], [score], _context()))[0]
    summary = asyncio.run(TechNewsSummarizer().summarize([enriched], _context()))

    assert "GitHub Releases" in summary
    assert "github_releases" not in summary
    assert enriched.why_it_matters.startswith("The release highlights")
    assert "faster inference paths" in enriched.why_it_matters
    assert "Release Notes" not in enriched.why_it_matters
    assert "updates #" not in enriched.why_it_matters
    assert "flagged this" not in enriched.why_it_matters


def test_deterministic_news_fallback_removes_title_prefix_and_dangling_fragments() -> None:
    item = _item(
        "news:aws",
        "Context intelligence in AWS Developer tools",
        "https://example.com/aws",
        source="rss",
        metadata={"feed_name": "AWS Machine Learning Blog"},
        raw_content=(
            "Context intelligence in AWS Developer tools: AWS describes context-aware "
            "developer workflows for teams building agents and."
        ),
    )
    score = asyncio.run(
        TechNewsScorer(TechNewsFiltersConfig(), TechNewsScoringConfig()).score([item], _context())
    )[0]

    enriched = asyncio.run(TechNewsEnricher().enrich([item], [score], _context()))[0]

    assert enriched.why_it_matters.startswith("The update describes")
    assert "Context intelligence in AWS Developer tools:" not in enriched.why_it_matters
    assert not enriched.why_it_matters.endswith("and.")


def test_enricher_keeps_deterministic_notes_when_llm_output_is_low_quality() -> None:
    item = _item(
        "news:hn",
        "AI Agent Guidelines for CS336 at Stanford",
        "https://example.com/agents",
        source="hackernews",
        metadata={"score": 500, "descendants": 90},
        raw_content="[aaaronic]: I think this one is overly verbose.",
    )
    score = asyncio.run(
        TechNewsScorer(TechNewsFiltersConfig(), TechNewsScoringConfig()).score([item], _context())
    )[0]

    enriched = asyncio.run(TechNewsEnricher(_LowQualityRanker()).enrich([item], [score], _context()))[0]

    assert "Hacker News" in enriched.why_it_matters
    assert "https:&#x2F;" not in enriched.why_it_matters
    assert "[aaaronic]:" not in enriched.learning_value
    assert enriched.action_items == [
        "Read the source article and the Hacker News discussion.",
        "Identify what changed and who is affected.",
        "Decide whether the story changes a tool, research, or product bet you are making.",
    ]


def test_markdown_rendering_is_stable_and_score_ordered() -> None:
    low = _item("news:low", "Low", "https://example.com/low").model_copy(
        update={"final_score": 3.0, "metadata": {"feed_name": "OpenAI News"}}
    )
    high = _item("news:high", "High", "https://example.com/high").model_copy(
        update={"final_score": 8.0}
    )

    summary = asyncio.run(TechNewsSummarizer().summarize([low, high], _context()))
    rendered = asyncio.run(TechNewsRenderer().render(summary, [low, high], _context()))

    assert summary.index("High") < summary.index("Low")
    assert "Selected 2 tech news item(s)." in summary
    assert "OpenAI News" in summary
    assert "/10" not in summary
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
    raw_content: str = "",
) -> SignalItem:
    return SignalItem(
        id=item_id,
        type="news",
        title=title,
        url=url,
        source=source,
        published_at=published_at or datetime(2026, 5, 26, tzinfo=timezone.utc),
        raw_content=raw_content,
        metadata=metadata or {},
    )


class _LowQualityRanker:
    async def analyze_items(self, items, prompt_builder, context):
        return {item.id: object() for item in items}

    def apply_analysis(self, item, analysis):
        return item.model_copy(
            update={
                "llm_score": 9.0,
                "final_score": 9.0,
                "why_it_matters": "https:&#x2F;&#x2F;example.com [aaaronic]: raw comment",
                "learning_value": "[aaaronic]: raw escaped discussion excerpt",
                "action_items": ["Copy this raw thread."],
            }
        )


class _RecordingRanker:
    def __init__(self) -> None:
        self.item_ids: list[str] = []

    async def analyze_items(self, items, prompt_builder, context):
        self.item_ids = [item.id for item in items]
        return {}

    def apply_analysis(self, item, analysis):
        return item


class _CredibilityClient:
    def is_configured(self) -> bool:
        return True

    async def complete_json(self, system_prompt: str, user_prompt: str) -> dict:
        return {
            "score": 8.0,
            "summary": "A concise LLM summary.",
            "why_it_matters": "A clean LLM reason.",
            "learning_value": "A clean LLM learning note.",
            "action_items": ["Read the primary source."],
            "source_credibility": "Likely true: primary source announcement.",
        }
