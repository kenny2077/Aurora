from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import httpx

from aurora.config import AuroraConfig, RunConfig, UnifiedDigestModeConfig
from aurora.config import ScholarModeConfig
from aurora.modes.scholar.scoring import ScholarEnricher
from aurora.modes.repo_learning.state import RepoLearningStateStore
from aurora.models import DeliveryResult, RenderedDigest, ScoreResult, SignalItem
from aurora.modes.unified_digest.render import UnifiedDigestRenderer, UnifiedDigestSummarizer, select_items
from aurora.modes.unified_digest.stages import (
    UnifiedDeduplicateStage,
    UnifiedDeliveryStage,
    UnifiedEnrichStage,
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
    context = StageContext(
        mode="unified_digest",
        run_id="test-run",
        config=config,
        until=datetime(2026, 5, 25, tzinfo=timezone.utc),
    )

    collected = asyncio.run(UnifiedFetchStage(config, builders).fetch(context))

    assert [item.id for item in collected] == ["news:1", "paper:1"]
    assert deliveries == []
    assert (tmp_path / "test-run" / "tech_news" / "enriched.jsonl").exists()
    assert (tmp_path / "test-run" / "scholar" / "enriched.jsonl").exists()
    assert [summary["mode"] for summary in context.metadata["unified_child_run_summaries"]] == [
        "tech_news",
        "scholar",
    ]


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
        section_limits={"news": 2, "repo": 1, "paper": 1},
        max_total_items=4,
        section_order=["news", "repo", "paper"],
    )
    items = [
        _item("news:1", "news", "News", 10.0),
        _item("news:2", "news", "Second News", 9.5),
        _item("paper:1", "paper", "Paper", 8.0),
        _item("repo:1", "repo", "Repo", 7.0),
        _item("repo:2", "repo", "Better Repo", 9.0),
    ]
    context = StageContext(mode="unified_digest", run_id="test")

    summary = asyncio.run(UnifiedDigestSummarizer(config).summarize(items, context))
    rendered = asyncio.run(UnifiedDigestRenderer(config).render(summary, items, context))

    assert summary.index("## Tech News") < summary.index("## GitHub Repos") < summary.index("## Research Papers")
    assert "Better Repo" in summary
    assert "Second News" in summary
    assert "[Repo](" not in summary
    assert rendered.metadata["selected_item_ids"] == ["news:1", "news:2", "repo:2", "paper:1"]
    assert rendered.metadata["recommended_repo_ids"] == ["repo:2"]
    assert rendered.metadata["item_counts"] == {"news": 2, "repo": 1, "paper": 1}
    assert rendered.html is not None
    assert "Today's Learning Path" not in rendered.html
    assert "Run diagnostics" not in rendered.html
    assert "aurora-repo-card" in rendered.html
    assert "web_html" in rendered.metadata


def test_unified_default_section_limits_select_five_news_three_repos_and_three_papers() -> None:
    config = UnifiedDigestModeConfig(max_total_items=20)
    items = [
        *[_item(f"news:{index}", "news", f"News {index}", 10.0 - index * 0.1) for index in range(6)],
        *[_item(f"repo:{index}", "repo", f"Repo {index}", 9.0 - index * 0.1) for index in range(4)],
        *[_item(f"paper:{index}", "paper", f"Paper {index}", 8.0 - index * 0.1) for index in range(4)],
    ]

    selected = select_items(items, config)

    assert [item.type for item in selected].count("news") == 5
    assert [item.type for item in selected].count("repo") == 3
    assert [item.type for item in selected].count("paper") == 3
    assert [item.id for item in selected] == [
        "news:0",
        "news:1",
        "news:2",
        "news:3",
        "news:4",
        "repo:0",
        "repo:1",
        "repo:2",
        "paper:0",
        "paper:1",
        "paper:2",
    ]


def test_unified_section_limit_falls_back_to_max_items_per_type_when_missing() -> None:
    config = UnifiedDigestModeConfig(
        max_items_per_type=2,
        max_total_items=10,
        section_limits={"news": 1},
    )
    items = [
        *[_item(f"news:{index}", "news", f"News {index}", 10.0 - index) for index in range(3)],
        *[_item(f"repo:{index}", "repo", f"Repo {index}", 9.0 - index) for index in range(3)],
        *[_item(f"paper:{index}", "paper", f"Paper {index}", 8.0 - index) for index in range(3)],
    ]

    selected = select_items(items, config)

    assert [item.type for item in selected].count("news") == 1
    assert [item.type for item in selected].count("repo") == 2
    assert [item.type for item in selected].count("paper") == 2


def test_unified_rendering_marks_cached_scholar_fallback_without_affecting_other_sections() -> None:
    config = UnifiedDigestModeConfig(
        max_items_per_type=8,
        max_total_items=20,
        section_order=["news", "repo", "paper"],
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
    assert "GitHub Repos" in summary
    assert "Tech News" in summary
    assert rendered.metadata["item_counts"] == {"paper": 1, "repo": 1, "news": 1}
    assert "Research Papers" in str(rendered.metadata["web_html"])
    assert "Tech News" in str(rendered.metadata["web_html"])


def test_unified_rendering_shows_repo_cards_with_evidence_blocks() -> None:
    config = UnifiedDigestModeConfig(
        max_items_per_type=8,
        max_total_items=20,
        section_order=["repo", "paper", "news"],
    )
    repo = _item(
        "repo:org/noisy",
        "repo",
        "org/noisy",
        9.0,
        url="https://github.com/org/noisy",
        metadata={
            "stars": 6000,
            "forks": 420,
            "open_issues": 12,
            "license": "MIT",
            "homepage": "https://noisy.example.com",
            "language": "Python",
            "topics": ["agents", "mcp"],
            "package_files": ["pyproject.toml", "examples/quickstart.py"],
            "recommendation_evidence": [
                "6k stars",
                "README found",
                "examples/quickstart.py",
            ],
            "quality_warnings": [
                "very high open issue count",
                "README or package files were not found during enrichment",
            ],
        },
        why_it_matters="org/noisy is worth studying because it has concrete learning evidence.",
        learning_value="Study examples/quickstart.py and pyproject.toml.",
        action_items=["Inspect examples/quickstart.py."],
    )
    paper = _item("paper:1", "paper", "Paper", 8.0)
    news = _item("news:1", "news", "News", 7.0)
    context = StageContext(mode="unified_digest", run_id="test")

    summary = asyncio.run(UnifiedDigestSummarizer(config).summarize([repo, paper, news], context))
    rendered = asyncio.run(UnifiedDigestRenderer(config).render(summary, [repo, paper, news], context))

    assert "### Repo to Study" not in summary
    assert "## GitHub Repos" in summary
    assert "   - 6k stars | 420 forks | 12 open issues" in summary
    assert "   - Value: org/noisy is worth studying because it has concrete learning evidence." in summary
    assert "   - Why:" not in summary
    assert "   - Study:" not in summary
    assert "license" not in summary
    assert "homepage" not in summary
    assert "   - Evidence:" not in summary
    assert "   - Files:" not in summary
    assert "Python" not in summary
    assert "agents" not in summary
    assert "   - Watch:" not in summary
    assert "## Research Papers" in summary
    assert "## Tech News" in summary
    assert "Evidence:" not in str(rendered.metadata["web_html"])
    assert "Files:" not in str(rendered.metadata["web_html"])
    assert "MIT license" not in str(rendered.metadata["web_html"])
    assert "homepage" not in str(rendered.metadata["web_html"])
    assert "Python" in str(rendered.metadata["web_html"])
    assert "agents" in str(rendered.metadata["web_html"])
    assert "<b>Why:</b>" not in str(rendered.metadata["web_html"])
    assert "<b>Value:</b>" in str(rendered.metadata["web_html"])
    assert "<b>Study:</b>" not in str(rendered.metadata["web_html"])
    assert "Watch:" not in str(rendered.metadata["web_html"])


def test_unified_rendering_cleans_legacy_news_learning_notes() -> None:
    config = UnifiedDigestModeConfig(
        max_items_per_type=8,
        max_total_items=20,
        section_order=["paper", "repo", "news"],
    )
    items = [
        _item("paper:1", "paper", "Paper", 8.0),
        _item("repo:1", "repo", "Repo", 7.0),
        _item(
            "news:1",
            "news",
            "AI Agent Guidelines for CS336 at Stanford",
            9.0,
            metadata={"score": 500, "descendants": 90},
            why_it_matters="",
            learning_value="",
            raw_content=(
                "https:&#x2F;&#x2F;example.com [aaaronic]: I think this one is "
                "overly verbose and probably falls out of context."
            ),
        ),
    ]
    context = StageContext(mode="unified_digest", run_id="test")

    summary = asyncio.run(UnifiedDigestSummarizer(config).summarize(items, context))

    assert "### News to Watch" not in summary
    assert "Hacker News" in summary
    assert "https:&#x2F;" not in summary
    assert "[aaaronic]:" not in summary
    assert "## Research Papers" in summary
    assert "## GitHub Repos" in summary


def test_unified_rendering_prefers_polished_news_notes() -> None:
    config = UnifiedDigestModeConfig(max_items_per_type=8, max_total_items=20)
    item = _item(
        "news:1",
        "news",
        "Polished News",
        9.0,
        why_it_matters="This is a clean reason for the digest.",
        learning_value="Use it to calibrate a practical product decision.",
        raw_content="[raw]: noisy thread text",
    )
    context = StageContext(mode="unified_digest", run_id="test")

    summary = asyncio.run(UnifiedDigestSummarizer(config).summarize([item], context))

    assert "This is a clean reason for the digest." in summary
    assert "Use it to calibrate a practical product decision." not in summary
    assert "[raw]:" not in summary
    assert "Credibility:" not in summary


def test_unified_summary_hides_run_summary_and_source_health_from_visible_digest() -> None:
    config = UnifiedDigestModeConfig(
        max_items_per_type=3,
        max_total_items=8,
        section_order=["news", "repo", "paper"],
    )
    context = StageContext(
        mode="unified_digest",
        run_id="test",
        metadata={
            "run_summary": {
                "counts": {
                    "raw": 3,
                    "normalized": 3,
                    "deduplicated": 2,
                    "score_results": 2,
                    "enriched": 2,
                },
                "source_health": {
                    "total": 2,
                    "ok": 1,
                    "failed": 1,
                    "rate_limited": 1,
                },
                "sources": [
                    {"source": "rss", "ok": True, "fetched_count": 2},
                    {
                        "source": "arxiv",
                        "ok": False,
                        "rate_limited": True,
                        "error": "429 Too Many Requests",
                    },
                ],
            },
            "unified_child_run_summaries": [
                {
                    "mode": "tech_news",
                    "counts": {"enriched": 1},
                    "source_health": {"ok": 1, "failed": 0, "rate_limited": 0},
                },
                {
                    "mode": "scholar",
                    "counts": {"enriched": 0},
                    "source_health": {"ok": 0, "failed": 1, "rate_limited": 1},
                    "warnings": [
                        "Semantic Scholar enrichment rate-limited; scoring continued with available metadata and AI analysis when configured."
                    ],
                },
            ],
        },
    )

    summary = asyncio.run(
        UnifiedDigestSummarizer(config).summarize(
            [_item("paper:1", "paper", "Paper", 8.0)],
            context,
        )
    )

    assert "## Today's Learning Path" not in summary
    assert "## Run Summary" not in summary
    assert "Items: 3 raw -> 3 normalized -> 2 deduplicated -> 2 enriched." not in summary
    assert "Sources: 1 ok, 1 failed, 1 rate limited." not in summary
    assert "arxiv failed: 429 Too Many Requests" not in summary
    assert "scholar warning: Semantic Scholar enrichment rate-limited" not in summary
    rendered = asyncio.run(
        UnifiedDigestRenderer(config).render(
            summary,
            [_item("paper:1", "paper", "Paper", 8.0)],
            context,
        )
    )
    assert "Run diagnostics" not in str(rendered.metadata["web_html"])
    assert "Semantic Scholar enrichment rate-limited" not in str(rendered.metadata["web_html"])


def test_unified_summary_hides_run_summary_when_no_items_survive() -> None:
    context = StageContext(
        mode="unified_digest",
        run_id="test",
        metadata={
            "run_summary": {
                "counts": {
                    "raw": 0,
                    "normalized": 0,
                    "deduplicated": 0,
                    "score_results": 0,
                    "enriched": 0,
                },
                "source_health": {
                    "total": 1,
                    "ok": 0,
                    "failed": 1,
                    "rate_limited": 0,
                },
                "sources": [
                    {"source": "unified_sources", "ok": False, "error": "no source data"}
                ],
            }
        },
    )

    summary = asyncio.run(
        UnifiedDigestSummarizer(UnifiedDigestModeConfig()).summarize([], context)
    )

    assert "No items were available for the unified digest." in summary
    assert "## Run Summary" not in summary
    assert "unified_sources failed: no source data" not in summary


def test_unified_summary_hides_cross_mode_connections_but_renderer_keeps_metadata() -> None:
    config = UnifiedDigestModeConfig(
        max_items_per_type=3,
        max_total_items=9,
        section_order=["paper", "repo", "news"],
    )
    paper = _item(
        "paper:agent-benchmark",
        "paper",
        "Agent Planning Benchmark",
        9.0,
        metadata={
            "code_urls": ["https://github.com/org/agent-kit"],
            "categories": ["cs.AI"],
        },
    ).model_copy(update={"tags": ["agents", "planning"]})
    repo = _item(
        "repo:org/agent-kit",
        "repo",
        "org/agent-kit",
        8.5,
        url="https://github.com/org/agent-kit",
        metadata={
            "full_name": "org/agent-kit",
            "topics": ["agents", "planning"],
        },
    ).model_copy(update={"tags": ["agents"]})
    news = _item(
        "news:agent-kit",
        "news",
        "Agent Kit gains attention",
        8.0,
        metadata={"tags": ["agents", "planning"]},
    ).model_copy(
        update={
            "raw_content": "Developers are discussing https://github.com/org/agent-kit for agent planning."
        }
    )

    context = StageContext(mode="unified_digest", run_id="test")
    summary = asyncio.run(UnifiedDigestSummarizer(config).summarize([paper, repo, news], context))
    rendered = asyncio.run(UnifiedDigestRenderer(config).render(summary, [paper, repo, news], context))

    assert "## Connections" not in summary
    assert "[Agent Planning Benchmark]" in summary
    assert "[org/agent-kit]" in summary
    assert "shared repository org/agent-kit" not in summary
    assert rendered.metadata["connections"]


def test_unified_renderer_metadata_includes_connections() -> None:
    config = UnifiedDigestModeConfig(
        max_items_per_type=3,
        max_total_items=9,
        section_order=["paper", "repo", "news"],
    )
    paper = _item(
        "paper:1",
        "paper",
        "Vision Agent Paper",
        9.0,
        metadata={"code_urls": ["https://github.com/org/vision-agent"]},
    ).model_copy(update={"tags": ["agents", "cv"]})
    repo = _item(
        "repo:org/vision-agent",
        "repo",
        "org/vision-agent",
        8.0,
        url="https://github.com/org/vision-agent",
        metadata={"full_name": "org/vision-agent", "topics": ["agents", "cv"]},
    )

    rendered = asyncio.run(
        UnifiedDigestRenderer(config).render(
            "summary",
            [paper, repo],
            StageContext(mode="unified_digest", run_id="test"),
        )
    )

    assert rendered.metadata["connections"] == [
        {
            "theme": "agents",
            "item_ids": ["paper:1", "repo:org/vision-agent"],
            "evidence_terms": ["agents", "cv", "org/vision-agent"],
            "reason": "shared repository org/vision-agent; shared specific signals: agents, cv",
        }
    ]


def test_unified_connections_ignore_single_generic_ai_ml_or_rl_overlap() -> None:
    config = UnifiedDigestModeConfig(
        max_items_per_type=3,
        max_total_items=9,
        section_order=["paper", "repo", "news"],
    )
    for generic_term in ("ai", "ml", "rl"):
        repo = _item(
            f"repo:org/{generic_term}",
            "repo",
            f"org/{generic_term}",
            8.0,
            url=f"https://github.com/org/{generic_term}",
            metadata={"full_name": f"org/{generic_term}", "topics": [generic_term]},
        ).model_copy(update={"tags": [generic_term]})
        news = _item(
            f"news:{generic_term}",
            "news",
            f"{generic_term.upper()} market update",
            9.0,
            metadata={"tags": [generic_term]},
        ).model_copy(update={"tags": [generic_term]})

        rendered = asyncio.run(
            UnifiedDigestRenderer(config).render(
                "summary",
                [repo, news],
                StageContext(mode="unified_digest", run_id="test"),
            )
        )

        assert rendered.metadata["connections"] == []


def test_unified_connections_require_multiple_specific_signals_without_repo() -> None:
    config = UnifiedDigestModeConfig(
        max_items_per_type=3,
        max_total_items=9,
        section_order=["paper", "repo", "news"],
    )
    paper = _item(
        "paper:segmentation",
        "paper",
        "Segmentation Benchmark",
        9.0,
        metadata={"categories": ["cs.CV"]},
    ).model_copy(update={"tags": ["segmentation"]})
    news = _item(
        "news:segmentation",
        "news",
        "Segmentation model gains attention",
        8.0,
        metadata={"tags": ["segmentation"]},
    ).model_copy(update={"tags": ["segmentation"]})

    rendered = asyncio.run(
        UnifiedDigestRenderer(config).render(
            "summary",
            [paper, news],
            StageContext(mode="unified_digest", run_id="test"),
        )
    )

    assert rendered.metadata["connections"] == []

    stronger_paper = paper.model_copy(update={"tags": ["segmentation", "benchmark"]})
    stronger_news = news.model_copy(update={"tags": ["segmentation", "benchmark"]})

    rendered = asyncio.run(
        UnifiedDigestRenderer(config).render(
            "summary",
            [stronger_paper, stronger_news],
            StageContext(mode="unified_digest", run_id="test"),
        )
    )

    assert rendered.metadata["connections"] == [
        {
            "theme": "benchmark",
            "item_ids": ["paper:segmentation", "news:segmentation"],
            "evidence_terms": ["benchmark", "segmentation"],
            "reason": "shared specific signals: benchmark, segmentation",
        }
    ]


def test_unified_connections_do_not_cluster_unrelated_items() -> None:
    config = UnifiedDigestModeConfig(
        max_items_per_type=3,
        max_total_items=9,
        section_order=["paper", "repo", "news"],
    )
    paper = _item(
        "paper:vision",
        "paper",
        "Vision Segmentation",
        9.0,
        metadata={"categories": ["cs.CV"]},
    ).model_copy(update={"tags": ["cv"]})
    repo = _item(
        "repo:org/scheduler",
        "repo",
        "org/scheduler",
        8.0,
        url="https://github.com/org/scheduler",
        metadata={"full_name": "org/scheduler", "topics": ["workflow-automation"]},
    )

    rendered = asyncio.run(
        UnifiedDigestRenderer(config).render(
            "summary",
            [paper, repo],
            StageContext(mode="unified_digest", run_id="test"),
        )
    )

    assert rendered.metadata["connections"] == []


def test_unified_summary_starts_with_tech_news_section_only() -> None:
    config = UnifiedDigestModeConfig(
        max_items_per_type=3,
        max_total_items=8,
        section_order=["news", "repo", "paper"],
    )
    items = [
        _item(
            "paper:1",
            "paper",
            "Strong Paper",
            9.0,
            why_it_matters="It explains a useful agent planning pattern.",
            learning_value="Study the evaluation setup.",
            action_items=["Read the method section.", "Compare the benchmark."],
        ),
        _item(
            "repo:1",
            "repo",
            "Useful Repo",
            8.5,
            why_it_matters="It shows the pattern in working code.",
            learning_value="Trace the package layout.",
            action_items=["Clone the repo.", "Run the examples."],
        ),
        _item("news:1", "news", "Top News", 8.0),
        _item("news:2", "news", "Second News", 7.0),
        _item("news:3", "news", "Third News", 6.0),
        _item("news:4", "news", "Fourth News", 5.0),
    ]

    summary = asyncio.run(
        UnifiedDigestSummarizer(config).summarize(
            items,
            StageContext(mode="unified_digest", run_id="test"),
        )
    )

    assert summary.startswith("# Aurora Unified Digest\n\n## Tech News")
    assert "## Today's Learning Path" not in summary
    assert "### Paper to Understand" not in summary
    assert "### Repo to Study" not in summary
    assert "### News to Watch" not in summary
    assert "It explains a useful agent planning pattern." in summary
    assert "Study the evaluation setup." in summary
    assert "Top News" in summary
    assert "Third News" in summary
    assert "Fourth News" in summary


def test_unified_paper_section_includes_analysis_actions_and_semantic_scholar_link() -> None:
    config = UnifiedDigestModeConfig(
        max_items_per_type=2,
        max_total_items=6,
        section_order=["paper", "repo", "news"],
    )
    paper = _item(
        "paper:1",
        "paper",
        "Analyzed Paper",
        9.0,
        metadata={"semantic_scholar_url": "https://www.semanticscholar.org/paper/S2"},
        why_it_matters="This clarifies an agent evaluation method.",
        learning_value="Study the benchmark design.",
        action_items=["Read the method.", "Compare the experiments."],
    )

    summary = asyncio.run(
        UnifiedDigestSummarizer(config).summarize(
            [paper],
            StageContext(mode="unified_digest", run_id="test"),
        )
    )

    section = summary.split("## Research Papers", maxsplit=1)[1]
    assert "Learn: Study the benchmark design." in section
    assert "Action: Read the method.; Compare the experiments." not in section
    assert "Semantic Scholar: https://www.semanticscholar.org/paper/S2" not in section


def test_unified_summary_omits_learning_path_missing_type_messages() -> None:
    config = UnifiedDigestModeConfig(
        max_items_per_type=3,
        max_total_items=8,
        section_order=["paper", "repo", "news"],
    )
    items = [
        _item(
            "paper:1",
            "paper",
            "Paper Without Actions",
            9.0,
            action_items=[],
        )
    ]

    summary = asyncio.run(
        UnifiedDigestSummarizer(config).summarize(
            items,
            StageContext(mode="unified_digest", run_id="test"),
        )
    )

    assert "## Today's Learning Path" not in summary
    assert "Read the abstract and identify the core claim." not in summary
    assert "No repository candidate is available for today's learning path." not in summary
    assert "No news item is available for today's learning path." not in summary
    assert "## Research Papers" in summary


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


def test_unified_delivery_records_all_selected_items_and_themes(tmp_path: Path) -> None:
    state_store = RepoLearningStateStore(tmp_path / "state.json")
    rendered = RenderedDigest(
        mode="unified_digest",
        title="Digest",
        markdown="body",
        metadata={
            "selected_item_ids": ["paper:one", "repo:org/one", "news:one"],
            "recommended_repo_ids": ["repo:org/one"],
            "connections": [
                {
                    "theme": "agents",
                    "item_ids": ["paper:one", "repo:org/one"],
                    "evidence_terms": ["agents", "planning"],
                    "reason": "shared specific signals: agents, planning",
                }
            ],
        },
    )
    when = datetime(2026, 5, 25, tzinfo=timezone.utc)

    results = asyncio.run(
        UnifiedDeliveryStage(state_store, _Deliver([])).deliver(
            rendered,
            StageContext(mode="unified_digest", run_id="test", until=when),
        )
    )

    assert results[0].metadata["selected_count"] == 3
    assert results[0].metadata["theme_count"] == 1
    assert state_store.recent_signal_ids(datetime(2026, 5, 24, tzinfo=timezone.utc)) == {
        "paper:one",
        "repo:org/one",
        "news:one",
    }
    assert state_store.recent_themes(datetime(2026, 5, 24, tzinfo=timezone.utc)) == {"agents"}


def test_unified_enrich_annotates_recently_seen_items(tmp_path: Path) -> None:
    state_store = RepoLearningStateStore(tmp_path / "state.json")
    state_store.mark_signals(
        ["paper:recent"],
        ["agents"],
        datetime(2026, 5, 25, tzinfo=timezone.utc),
    )
    config = AuroraConfig(
        run=RunConfig(state_path=tmp_path / "state.json"),
        modes={"repo_learning": {"ranking": {"history_lookback_days": 14}}},
    )
    recent = _item("paper:recent", "paper", "Recent Paper", 8.0)
    fresh = _item("paper:fresh", "paper", "Fresh Paper", 9.0)

    enriched = asyncio.run(
        UnifiedEnrichStage().enrich(
            [recent, fresh],
            [],
            StageContext(
                mode="unified_digest",
                run_id="test",
                config=config,
                until=datetime(2026, 5, 26, tzinfo=timezone.utc),
            ),
        )
    )

    assert enriched[0].metadata["recently_seen"] is True
    assert "recently_seen" not in enriched[1].metadata


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


def test_unified_fetch_keeps_scholar_papers_when_semantic_scholar_rate_limits(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", "test-key")
    requests: list[httpx.Request] = []
    sleep_calls: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    monkeypatch.setattr("aurora.modes.scholar.semantic_scholar.asyncio.sleep", fake_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(429, json={"message": "Too Many Requests"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    config = AuroraConfig(
        run=RunConfig(output_dir=tmp_path, cache_dir=tmp_path / "cache"),
        modes={"unified_digest": {"include_modes": ["scholar"]}},
    )
    context = StageContext(mode="unified_digest", run_id="test", config=config)

    async def exercise() -> list[SignalItem]:
        try:
            return await UnifiedFetchStage(
                config,
                {"scholar": lambda config: _semantic_scholar_pipeline(config, client)},
            ).fetch(context)
        finally:
            await client.aclose()

    collected = asyncio.run(exercise())

    assert len(requests) == 3
    assert sleep_calls == [1.0, 1.25, 1.0, 1.25]
    assert [item.id for item in collected] == ["paper:semantic"]
    assert "unified_mode_failures" not in context.metadata
    child_summary = context.metadata["unified_child_run_summaries"][0]
    assert child_summary["warnings"] == [
        "Semantic Scholar enrichment rate-limited; scoring continued with available metadata and AI analysis when configured."
    ]


def test_unified_fetch_does_not_leak_scholar_warnings_into_later_modes(
    tmp_path: Path,
) -> None:
    config = AuroraConfig(
        run=RunConfig(output_dir=tmp_path),
        modes={
            "unified_digest": {
                "include_modes": ["scholar", "repo_learning"],
                "section_order": ["paper", "repo", "news"],
            }
        },
    )
    context = StageContext(mode="unified_digest", run_id="test", config=config)

    collected = asyncio.run(
        UnifiedFetchStage(
            config,
            {
                "scholar": lambda config: _warning_pipeline(
                    "scholar",
                    [_item("paper:1", "paper", "Paper", 8.0)],
                    "Semantic Scholar enrichment rate-limited; scoring continued with available metadata and AI analysis when configured.",
                ),
                "repo_learning": lambda config: _static_pipeline(
                    "repo_learning",
                    [_item("repo:1", "repo", "Repo", 7.0)],
                    [],
                ),
            },
        ).fetch(context)
    )

    assert [item.id for item in collected] == ["paper:1", "repo:1"]
    child_summaries = context.metadata["unified_child_run_summaries"]
    assert child_summaries[0]["mode"] == "scholar"
    assert child_summaries[0]["warnings"] == [
        "Semantic Scholar enrichment rate-limited; scoring continued with available metadata and AI analysis when configured."
    ]
    assert child_summaries[1]["mode"] == "repo_learning"
    assert "warnings" not in child_summaries[1]


def _item(
    item_id: str,
    item_type: str,
    title: str,
    score: float,
    *,
    url: str | None = None,
    metadata: dict | None = None,
    why_it_matters: str | None = None,
    learning_value: str | None = None,
    action_items: list[str] | None = None,
    raw_content: str | None = None,
) -> SignalItem:
    return SignalItem(
        id=item_id,
        type=item_type,
        title=title,
        url=url or f"https://example.com/{item_id.replace(':', '-')}",
        source="test",
        published_at=datetime(2026, 5, 25, tzinfo=timezone.utc),
        raw_content=raw_content if raw_content is not None else f"{title} content",
        metadata=metadata or {},
        deterministic_score=score,
        final_score=score,
        why_it_matters=why_it_matters if why_it_matters is not None else f"{title} matters",
        learning_value=learning_value or "",
        action_items=action_items or [],
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


def _warning_pipeline(mode: str, items: list[SignalItem], warning: str) -> ModePipeline:
    return ModePipeline(
        mode=mode,
        fetch_stages=[_Fetch(items)],
        normalize_stage=_Normalize(),
        deduplicate_stage=_Dedup(),
        score_stage=_Score(),
        enrich_stage=_WarningEnrich(warning),
        summarize_stage=_Summarize(),
        render_stage=_Render(),
        deliver_stage=_Deliver([]),
    )


def _semantic_scholar_pipeline(config: AuroraConfig, client: httpx.AsyncClient) -> ModePipeline:
    paper = _item(
        "paper:semantic",
        "paper",
        "Semantic Paper",
        8.0,
        metadata={"source_ids": {"arxiv": "2606.1"}},
    )
    return ModePipeline(
        mode="scholar",
        fetch_stages=[_Fetch([paper])],
        normalize_stage=_Normalize(),
        deduplicate_stage=_Dedup(),
        score_stage=_Score(),
        enrich_stage=ScholarEnricher(config.modes.scholar, http_client=client),
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


class _WarningEnrich:
    def __init__(self, warning: str) -> None:
        self.warning = warning

    async def enrich(self, items, score_results, context: StageContext) -> list[SignalItem]:
        context.metadata.setdefault("semantic_scholar_warnings", []).append(self.warning)
        return list(items)


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
