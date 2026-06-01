from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from aurora.config import AuroraConfig, RunConfig, ScholarModeConfig
from aurora.modes.scholar.cache import CACHE_RELATIVE_PATH
from aurora.modes.scholar.fields import (
    expanded_arxiv_categories,
    expanded_keyword_allowlist,
    expanded_venue_allowlist,
)
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


def _cached_context(tmp_path: Path, *, until: datetime | None = None) -> StageContext:
    return StageContext(
        mode="scholar",
        run_id="test",
        until=until or datetime(2026, 5, 26, tzinfo=timezone.utc),
        config=AuroraConfig(run=RunConfig(cache_dir=tmp_path / "cache")),
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


def test_research_field_presets_expand_categories_keywords_venues_and_tags() -> None:
    config = ScholarModeConfig(fields=["cv"], keyword_allowlist=["diffusion"], venue_allowlist=["ICML"])
    paper = _paper(
        "cv",
        "Vision-Language Segmentation",
        {"categories": ["cs.CV"], "venue": "CVPR", "venue_year": 2026, "source_ids": {}},
        abstract="A computer vision method for segmentation and vision-language evaluation.",
    )

    score = asyncio.run(ScholarScorer(config).score([paper], _context()))[0]

    assert "cs.CV" in expanded_arxiv_categories(config)
    assert "computer vision" in expanded_keyword_allowlist(config)
    assert "CVPR" in expanded_venue_allowlist(config)
    assert "cv" in score.tags
    assert score.score_breakdown["topic_relevance_signal"] > 0.5


def test_enricher_applies_score_and_fallback_learning_text() -> None:
    item = _paper("paper", "Reasoning", {"venue": "ICLR", "source_ids": {"arxiv": "1"}})
    score = asyncio.run(ScholarScorer(ScholarModeConfig()).score([item], _context()))[0]

    enriched = asyncio.run(ScholarEnricher().enrich([item], [score], _context()))

    assert enriched[0].final_score == score.final_score
    assert enriched[0].llm_score is None
    assert enriched[0].why_it_matters
    assert enriched[0].learning_value
    assert "score_breakdown" in enriched[0].metadata


def test_enricher_writes_successful_scholar_cache(tmp_path: Path) -> None:
    item = _paper("paper", "Reasoning", {"venue": "ICLR", "source_ids": {"arxiv": "1"}})
    config = ScholarModeConfig()
    score = asyncio.run(ScholarScorer(config).score([item], _context()))[0]
    context = _cached_context(tmp_path)

    enriched = asyncio.run(ScholarEnricher(config).enrich([item], [score], context))

    cache_path = tmp_path / "cache" / CACHE_RELATIVE_PATH
    assert cache_path.exists()
    assert enriched[0].id in cache_path.read_text(encoding="utf-8")


def test_enricher_loads_recent_cached_papers_when_live_results_empty(tmp_path: Path) -> None:
    item = _paper("paper", "Cached Paper", {"venue": "ICLR", "source_ids": {"arxiv": "1"}}).model_copy(
        update={"final_score": 8.0, "why_it_matters": "Cached reason."}
    )
    config = ScholarModeConfig()
    context = _cached_context(tmp_path)
    asyncio.run(ScholarEnricher(config).enrich([item], [], context))

    fallback = asyncio.run(ScholarEnricher(config).enrich([], [], context))

    assert [item.id for item in fallback] == ["paper"]
    assert fallback[0].metadata["cached_fallback"] is True
    assert fallback[0].why_it_matters == "Cached reason."
    assert context.metadata["scholar_cached_fallback_used"] is True


def test_enricher_ignores_expired_or_corrupt_scholar_cache(tmp_path: Path) -> None:
    config = ScholarModeConfig(fallback_cache_ttl_hours=1)
    context = _cached_context(tmp_path, until=datetime(2026, 5, 26, tzinfo=timezone.utc))
    cache_path = tmp_path / "cache" / CACHE_RELATIVE_PATH
    cache_path.parent.mkdir(parents=True)
    cache_path.write_text("not-json\n", encoding="utf-8")

    corrupt = asyncio.run(ScholarEnricher(config).enrich([], [], context))
    assert corrupt == []

    item = _paper("paper", "Expired Paper", {"source_ids": {"arxiv": "1"}}).model_copy(
        update={"final_score": 8.0}
    )
    asyncio.run(ScholarEnricher(config).enrich([item], [], context))
    old_timestamp = (context.until - timedelta(hours=2)).timestamp()
    os.utime(cache_path, (old_timestamp, old_timestamp))

    expired = asyncio.run(ScholarEnricher(config).enrich([], [], context))
    assert expired == []


def test_semantic_scholar_enrichment_skips_when_api_key_missing(monkeypatch) -> None:
    monkeypatch.delenv("SEMANTIC_SCHOLAR_API_KEY", raising=False)
    item = _paper("paper", "Reasoning", {"source_ids": {"arxiv": "2605.1"}})
    score = asyncio.run(ScholarScorer(ScholarModeConfig()).score([item], _context()))[0]

    enriched = asyncio.run(ScholarEnricher(ScholarModeConfig()).enrich([item], [score], _context()))

    assert enriched[0].metadata.get("semantic_scholar_paper_id") is None
    assert enriched[0].metadata.get("citation_count") is None


def test_semantic_scholar_enrichment_fills_citation_metadata(monkeypatch) -> None:
    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", "test-key")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["x-api-key"] == "test-key"
        return httpx.Response(
            200,
            json={
                "paperId": "S2-123",
                "url": "https://www.semanticscholar.org/paper/S2-123",
                "citationCount": 42,
                "influentialCitationCount": 7,
                "externalIds": {"ArXiv": "2605.1", "DOI": "10.1/test"},
            },
        )

    item = _paper("paper", "Reasoning", {"source_ids": {"arxiv": "2605.1"}})
    score = asyncio.run(ScholarScorer(ScholarModeConfig()).score([item], _context()))[0]

    async def exercise() -> list[SignalItem]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await ScholarEnricher(ScholarModeConfig(), http_client=client).enrich(
                [item],
                [score],
                _context(),
            )

    enriched = asyncio.run(exercise())

    metadata = enriched[0].metadata
    assert len(requests) == 1
    assert metadata["semantic_scholar_paper_id"] == "S2-123"
    assert metadata["semantic_scholar_url"] == "https://www.semanticscholar.org/paper/S2-123"
    assert metadata["citation_count"] == 42
    assert metadata["influential_citation_count"] == 7
    assert metadata["source_ids"]["semantic_scholar"] == "S2-123"
    assert metadata["source_ids"]["doi"] == "10.1/test"


def test_semantic_scholar_enrichment_honors_request_cap(monkeypatch) -> None:
    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", "test-key")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"paperId": "S2", "citationCount": 1})

    config = ScholarModeConfig(sources={"semantic_scholar": {"max_requests_per_run": 1}})
    items = [
        _paper("first", "First", {"source_ids": {"arxiv": "2605.1"}}),
        _paper("second", "Second", {"source_ids": {"arxiv": "2605.2"}}),
    ]
    scores = asyncio.run(ScholarScorer(config).score(items, _context()))

    async def exercise() -> list[SignalItem]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await ScholarEnricher(config, http_client=client).enrich(items, scores, _context())

    enriched = asyncio.run(exercise())

    assert len(requests) == 1
    assert enriched[0].metadata["semantic_scholar_paper_id"] == "S2"
    assert enriched[1].metadata.get("semantic_scholar_paper_id") is None


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
