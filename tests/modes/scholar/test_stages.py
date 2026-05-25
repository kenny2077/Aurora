from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from aurora.config import ScholarModeConfig
from aurora.modes.scholar.prompts import RESEARCH_ANALYSIS_SYSTEM, RESEARCH_ANALYSIS_USER
from aurora.modes.scholar.render import ScholarRenderer, ScholarSummarizer
from aurora.modes.scholar.scoring import ScholarEnricher, ScholarScorer
from aurora.modes.scholar.stages import ScholarDeduplicateStage, ScholarNormalizeStage
from aurora.models import SignalItem
from aurora.pipeline import StageContext


def _context() -> StageContext:
    return StageContext(
        mode="scholar",
        run_id="test",
        since=datetime(2026, 5, 25, tzinfo=timezone.utc),
        until=datetime(2026, 5, 26, tzinfo=timezone.utc),
    )


def test_normalize_converts_records_to_paper_signal_items() -> None:
    raw = [
        {
            "id": "arxiv:2605.12345",
            "source": "arxiv",
            "title": "LLM Agents",
            "url": "https://arxiv.org/abs/2605.12345",
            "published_at": datetime(2026, 5, 26, tzinfo=timezone.utc),
            "abstract": "A useful abstract.",
            "metadata": {"authors": ["Ada"], "source_ids": {"arxiv": "2605.12345"}},
        }
    ]

    items = asyncio.run(ScholarNormalizeStage().normalize(raw, _context()))

    assert items == [
        SignalItem(
            id="arxiv:2605.12345",
            type="paper",
            title="LLM Agents",
            url="https://arxiv.org/abs/2605.12345",
            source="arxiv",
            published_at=datetime(2026, 5, 26, tzinfo=timezone.utc),
            raw_content="A useful abstract.",
            metadata={"authors": ["Ada"], "source_ids": {"arxiv": "2605.12345"}},
        )
    ]


def test_deduplicate_collapses_doi_arxiv_openreview_and_title_duplicates() -> None:
    items = [
        _paper("p1", "Shared Title", {"source_ids": {"doi": "10.1/test"}}),
        _paper("p2", "Other Title", {"source_ids": {"doi": "10.1/test"}}),
        _paper("p3", "Arxiv Title", {"source_ids": {"arxiv": "2605.1"}}),
        _paper("p4", "Different", {"source_ids": {"arxiv": "2605.1"}}),
        _paper("p5", "Title Only", {"source_ids": {}}),
        _paper("p6", "Title Only", {"source_ids": {}}),
    ]

    deduped = asyncio.run(ScholarDeduplicateStage().deduplicate(items, _context()))

    assert [item.id for item in deduped] == ["p1", "p3", "p5"]


def test_scoring_blocklists_and_rewards_top_venue_code_keyword_papers() -> None:
    config = ScholarModeConfig(keyword_allowlist=["reasoning", "benchmark"])
    scorer = ScholarScorer(config)
    strong = _paper(
        "strong",
        "Reasoning Benchmark",
        {
            "venue": "ICLR",
            "venue_year": 2026,
            "status": "accepted",
            "categories": ["cs.AI"],
            "code_urls": ["https://github.com/org/repo"],
            "project_urls": ["https://paper.example.com"],
            "source_ids": {"arxiv": "2605.1", "doi": "10.1/test"},
            "citation_count": 25,
            "influential_citation_count": 5,
        },
        abstract="We introduce a reasoning benchmark with evaluation, baseline, ablation, method, and result evidence.",
    )
    weak = _paper(
        "weak",
        "Old Application",
        {"venue_year": 2020, "status": "unknown", "source_ids": {}},
        abstract="Short abstract.",
    )
    blocked = _paper(
        "blocked",
        "Medical Case Report",
        {"source_ids": {}},
        abstract="This is a medical case report.",
    )

    strong_score, weak_score, blocked_score = asyncio.run(
        scorer.score([strong, weak, blocked], _context())
    )

    assert strong_score.final_score > weak_score.final_score
    assert blocked_score.final_score == 0.0
    assert set(strong_score.score_breakdown) == {
        "venue_signal",
        "novelty_signal",
        "recency_signal",
        "code_signal",
        "citation_signal",
        "topic_relevance_signal",
        "learning_value_signal",
        "source_diversity_signal",
    }


def test_enricher_applies_score_and_fallback_learning_text() -> None:
    item = _paper("paper", "Reasoning", {"venue": "ICLR", "source_ids": {"arxiv": "1"}})
    score = asyncio.run(ScholarScorer(ScholarModeConfig()).score([item], _context()))[0]

    enriched = asyncio.run(ScholarEnricher().enrich([item], [score], _context()))

    assert enriched[0].final_score == score.final_score
    assert enriched[0].llm_score is None
    assert enriched[0].why_it_matters
    assert enriched[0].learning_value
    assert "score_breakdown" in enriched[0].metadata


def test_markdown_rendering_is_stable_score_ordered_and_capped() -> None:
    config = ScholarModeConfig(final_item_count=1, score_threshold=0)
    low = _paper("low", "Low Paper", {"authors": ["Low"]}).model_copy(update={"final_score": 4.0})
    high = _paper("high", "High Paper", {"authors": ["High"], "venue": "ICLR", "status": "accepted"}).model_copy(
        update={"final_score": 9.0}
    )

    summary = asyncio.run(ScholarSummarizer(config).summarize([low, high], _context()))
    rendered = asyncio.run(ScholarRenderer().render(summary, [low, high], _context()))

    assert "Selected 1 research paper(s)." in summary
    assert "High Paper" in summary
    assert "Low Paper" not in summary
    assert rendered.mode == "scholar"
    assert rendered.markdown == summary


def test_scholar_prompt_constants_include_required_json_fields() -> None:
    assert '"score"' in RESEARCH_ANALYSIS_SYSTEM
    assert '"why_it_matters"' in RESEARCH_ANALYSIS_SYSTEM
    assert "{abstract}" in RESEARCH_ANALYSIS_USER
    assert "{deterministic_score}" in RESEARCH_ANALYSIS_USER


def _paper(
    item_id: str,
    title: str,
    metadata: dict,
    *,
    abstract: str = "We introduce a method with evaluation and benchmark results for reasoning.",
) -> SignalItem:
    return SignalItem(
        id=item_id,
        type="paper",
        title=title,
        url=f"https://example.com/{item_id}",
        source="arxiv",
        published_at=datetime(2026, 5, 26, tzinfo=timezone.utc),
        raw_content=abstract,
        metadata=metadata,
    )

