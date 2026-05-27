from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from aurora.config import AuroraConfig, RunConfig, UnifiedDigestModeConfig
from aurora.modes.repo_learning.state import RepoLearningStateStore
from aurora.models import DeliveryResult, RenderedDigest, ScoreResult, SignalItem
from aurora.modes.unified_digest.render import UnifiedDigestRenderer, UnifiedDigestSummarizer
from aurora.modes.unified_digest.stages import (
    UnifiedDeduplicateStage,
    UnifiedDeliveryStage,
    UnifiedFetchStage,
)
from aurora.pipeline import ModePipeline, PipelineRunner, StageContext


def test_unified_fetch_collects_enriched_items_without_sub_delivery(tmp_path: Path) -> None:
    deliveries: list[str] = []
    config = AuroraConfig(
        run=RunConfig(output_dir=tmp_path),
        modes={
            "unified_digest": {
                "include_modes": ["tech_news", "scholar"],
                "section_order": ["paper", "repo", "news"],
            }
        },
    )
    builders = {
        "tech_news": lambda config: _static_pipeline(
            "tech_news", [_item("news:1", "news", "News", 8.0)], deliveries
        ),
        "scholar": lambda config: _static_pipeline(
            "scholar", [_item("paper:1", "paper", "Paper", 9.0)], deliveries
        ),
    }

    collected = asyncio.run(
        UnifiedFetchStage(config, builders).fetch(
            StageContext(
                mode="unified_digest",
                run_id="test-run",
                config=config,
                until=datetime(2026, 5, 25, tzinfo=timezone.utc),
            )
        )
    )

    assert [item.id for item in collected] == ["news:1", "paper:1"]
    assert deliveries == []
    assert (tmp_path / "test-run" / "tech_news" / "enriched.jsonl").exists()
    assert (tmp_path / "test-run" / "scholar" / "enriched.jsonl").exists()


def test_cross_mode_dedup_collapses_url_title_paper_and_repo_duplicates() -> None:
    items = [
        _item("news:1", "news", "Shared Title", 6.0, url="https://www.example.com/story/"),
        _item("news:2", "news", "Other", 9.0, url="https://example.com/story"),
        _item("paper:1", "paper", "Paper", 8.0, metadata={"source_ids": {"doi": "10.1/test"}}),
        _item("paper:2", "paper", "Paper Copy", 7.0, metadata={"source_ids": {"doi": "10.1/test"}}),
        _item("repo:1", "repo", "org/repo", 5.0, metadata={"full_name": "org/repo"}),
        _item("repo:2", "repo", "ORG/REPO", 6.0, metadata={"full_name": "ORG/REPO"}),
    ]

    deduped = asyncio.run(
        UnifiedDeduplicateStage(UnifiedDigestModeConfig()).deduplicate(
            items,
            StageContext(mode="unified_digest", run_id="test"),
        )
    )

    assert [item.id for item in deduped] == ["news:2", "paper:1", "repo:2"]


def test_unified_rendering_respects_section_order_and_caps() -> None:
    config = UnifiedDigestModeConfig(
        max_items_per_type=1,
        max_total_items=2,
        section_order=["repo", "paper", "news"],
    )
    items = [
        _item("news:1", "news", "News", 10.0),
        _item("paper:1", "paper", "Paper", 8.0),
        _item("repo:1", "repo", "Repo", 7.0),
        _item("repo:2", "repo", "Better Repo", 9.0),
    ]
    context = StageContext(mode="unified_digest", run_id="test")

    summary = asyncio.run(UnifiedDigestSummarizer(config).summarize(items, context))
    rendered = asyncio.run(UnifiedDigestRenderer(config).render(summary, items, context))

    assert summary.index("Repositories") < summary.index("Research Papers")
    assert "Better Repo" in summary
    assert "[Repo](" not in summary
    assert "News" not in summary
    assert rendered.metadata["selected_item_ids"] == ["repo:2", "paper:1"]
    assert rendered.metadata["recommended_repo_ids"] == ["repo:2"]
    assert rendered.metadata["item_counts"] == {"repo": 1, "paper": 1, "news": 0}


def test_unified_rendering_marks_cached_scholar_fallback_without_affecting_other_sections() -> None:
    config = UnifiedDigestModeConfig(
        max_items_per_type=8,
        max_total_items=20,
        section_order=["paper", "repo", "news"],
    )
    items = [
        _item(
            "paper:cached",
            "paper",
            "Cached Paper",
            8.0,
            metadata={"cached_fallback": True},
        ),
        _item("repo:1", "repo", "Repo", 7.0),
        _item("news:1", "news", "News", 6.0),
    ]
    context = StageContext(mode="unified_digest", run_id="test")

    summary = asyncio.run(UnifiedDigestSummarizer(config).summarize(items, context))
    rendered = asyncio.run(UnifiedDigestRenderer(config).render(summary, items, context))

    assert "Using cached scholar results because live sources returned no papers." in summary
    assert "Repositories" in summary
    assert "Tech News" in summary
    assert rendered.metadata["item_counts"] == {"paper": 1, "repo": 1, "news": 1}


def test_unified_delivery_updates_repo_recommendation_state(tmp_path: Path) -> None:
    state_store = RepoLearningStateStore(tmp_path / "state.json")
    rendered = RenderedDigest(
        mode="unified_digest",
        title="Digest",
        markdown="body",
        metadata={"recommended_repo_ids": ["repo:org/one", "repo:org/two"]},
    )
    context = StageContext(
        mode="unified_digest",
        run_id="test",
        until=datetime(2026, 5, 25, tzinfo=timezone.utc),
    )

    results = asyncio.run(UnifiedDeliveryStage(state_store, _Deliver([])).deliver(rendered, context))
    recent = state_store.recent_ids(datetime(2026, 5, 24, tzinfo=timezone.utc))

    assert [result.channel for result in results] == ["repo_learning_state", "test"]
    assert results[0].metadata["recommended_count"] == 2
    assert recent == {"repo:org/one", "repo:org/two"}


def test_unified_pipeline_reports_included_mode_failures(tmp_path: Path) -> None:
    config = AuroraConfig(
        run=RunConfig(output_dir=tmp_path),
        modes={"unified_digest": {"include_modes": ["scholar"]}},
    )
    context = StageContext(mode="unified_digest", run_id="test", config=config)
    pipeline = ModePipeline(
        mode="unified_digest",
        fetch_stages=[UnifiedFetchStage(config, {"scholar": _failing_builder})],
        normalize_stage=_Normalize(),
        deduplicate_stage=UnifiedDeduplicateStage(config.modes.unified_digest),
        score_stage=_Score(),
        enrich_stage=_Enrich(),
        summarize_stage=_Summarize(),
        render_stage=_Render(),
        deliver_stage=_Deliver([]),
    )

    result = asyncio.run(
        PipelineRunner(output_dir=tmp_path).run(
            pipeline,
            context,
        )
    )

    assert result.raw_count == 0
    assert result.source_statuses[0].ok is True
    assert context.metadata["unified_mode_failures"] == [
        {"mode": "scholar", "error": "scholar mode is disabled"}
    ]


def test_unified_fetch_keeps_successful_modes_when_one_mode_fails(tmp_path: Path) -> None:
    config = AuroraConfig(
        run=RunConfig(output_dir=tmp_path),
        modes={"unified_digest": {"include_modes": ["tech_news", "scholar"]}},
    )
    context = StageContext(mode="unified_digest", run_id="test", config=config)
    builders = {
        "tech_news": lambda config: _static_pipeline(
            "tech_news", [_item("news:1", "news", "News", 8.0)], []
        ),
        "scholar": _failing_builder,
    }

    collected = asyncio.run(UnifiedFetchStage(config, builders).fetch(context))

    assert [item.id for item in collected] == ["news:1"]
    assert context.metadata["unified_mode_failures"] == [
        {"mode": "scholar", "error": "scholar mode is disabled"}
    ]


def test_unified_fetch_collects_cached_scholar_papers(tmp_path: Path) -> None:
    cached_paper = _item(
        "paper:cached",
        "paper",
        "Cached Paper",
        8.0,
        metadata={"cached_fallback": True},
    )
    config = AuroraConfig(
        run=RunConfig(output_dir=tmp_path),
        modes={"unified_digest": {"include_modes": ["scholar"]}},
    )
    context = StageContext(mode="unified_digest", run_id="test", config=config)

    collected = asyncio.run(
        UnifiedFetchStage(
            config,
            {"scholar": lambda config: _cached_pipeline("scholar", cached_paper)},
        ).fetch(context)
    )

    assert [item.id for item in collected] == ["paper:cached"]
    assert collected[0].metadata["cached_fallback"] is True


def _item(
    item_id: str,
    item_type: str,
    title: str,
    score: float,
    *,
    url: str | None = None,
    metadata: dict | None = None,
) -> SignalItem:
    return SignalItem(
        id=item_id,
        type=item_type,
        title=title,
        url=url or f"https://example.com/{item_id.replace(':', '-')}",
        source="test",
        published_at=datetime(2026, 5, 25, tzinfo=timezone.utc),
        raw_content=f"{title} content",
        metadata=metadata or {},
        deterministic_score=score,
        final_score=score,
        why_it_matters=f"{title} matters",
    )


def _static_pipeline(
    mode: str, items: list[SignalItem], deliveries: list[str]
) -> ModePipeline:
    return ModePipeline(
        mode=mode,
        fetch_stages=[_Fetch(items)],
        normalize_stage=_Normalize(),
        deduplicate_stage=_Dedup(),
        score_stage=_Score(),
        enrich_stage=_Enrich(),
        summarize_stage=_Summarize(),
        render_stage=_Render(),
        deliver_stage=_Deliver(deliveries),
    )


def _failing_builder(config: AuroraConfig) -> ModePipeline:
    raise ValueError("scholar mode is disabled")


def _cached_pipeline(mode: str, item: SignalItem) -> ModePipeline:
    return ModePipeline(
        mode=mode,
        fetch_stages=[_Fetch([])],
        normalize_stage=_Normalize(),
        deduplicate_stage=_Dedup(),
        score_stage=_Score(),
        enrich_stage=_CachedEnrich(item),
        summarize_stage=_Summarize(),
        render_stage=_Render(),
        deliver_stage=_Deliver([]),
    )


class _Fetch:
    name = "static"

    def __init__(self, items: list[SignalItem]) -> None:
        self.items = items

    async def fetch(self, context: StageContext) -> list[SignalItem]:
        return self.items


class _Normalize:
    async def normalize(self, raw_items, context: StageContext) -> list[SignalItem]:
        return list(raw_items)


class _Dedup:
    async def deduplicate(self, items, context: StageContext) -> list[SignalItem]:
        return list(items)


class _Score:
    async def score(self, items, context: StageContext) -> list[ScoreResult]:
        return [
            ScoreResult(item_id=item.id, deterministic_score=item.deterministic_score, final_score=item.final_score)
            for item in items
        ]


class _Enrich:
    async def enrich(self, items, score_results, context: StageContext) -> list[SignalItem]:
        return list(items)


class _CachedEnrich:
    def __init__(self, item: SignalItem) -> None:
        self.item = item

    async def enrich(self, items, score_results, context: StageContext) -> list[SignalItem]:
        return [self.item]


class _Summarize:
    async def summarize(self, items, context: StageContext) -> str:
        return "summary"


class _Render:
    async def render(self, summary, items, context: StageContext) -> RenderedDigest:
        return RenderedDigest(mode=context.mode, title=context.mode, markdown=summary)


class _Deliver:
    def __init__(self, deliveries: list[str]) -> None:
        self.deliveries = deliveries

    async def deliver(self, rendered, context: StageContext) -> list[DeliveryResult]:
        self.deliveries.append(context.mode)
        return [DeliveryResult(channel="test")]
