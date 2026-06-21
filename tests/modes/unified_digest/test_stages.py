from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

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
from aurora.modes.unified_digest.quality import (
    audit_rendered_public_digest,
    public_copy_quality,
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


def test_unified_news_selection_prefers_source_variety_before_repeat_sources() -> None:
    config = UnifiedDigestModeConfig(
        max_total_items=10,
        section_limits={"news": 5, "repo": 1, "paper": 1},
    )

    selected = select_items(
        [
            _item("repo:1", "repo", "Repo", 7.0),
            _item("paper:1", "paper", "Paper", 7.0),
            _item("news:hn-1", "news", "HN 1", 9.9, source="hackernews"),
            _item("news:hn-2", "news", "HN 2", 9.8, source="hackernews"),
            _item("news:hn-3", "news", "HN 3", 9.7, source="hackernews"),
            _item(
                "news:openai",
                "news",
                "OpenAI",
                7.0,
                source="rss",
                metadata={"feed_name": "OpenAI News"},
            ),
            _item(
                "news:simon",
                "news",
                "Simon",
                6.9,
                source="rss",
                metadata={"feed_name": "Simon Willison"},
            ),
        ],
        config,
    )

    assert [item.id for item in selected if item.type == "news"] == [
        "news:hn-1",
        "news:openai",
        "news:simon",
        "news:hn-2",
        "news:hn-3",
    ]


def test_unified_news_selection_does_not_promote_weak_sources_for_variety() -> None:
    config = UnifiedDigestModeConfig(
        max_total_items=10,
        section_limits={"news": 3, "repo": 1, "paper": 1},
    )

    selected = select_items(
        [
            _item("repo:1", "repo", "Repo", 7.0),
            _item("paper:1", "paper", "Paper", 7.0),
            _item("news:hn-1", "news", "HN 1", 9.9, source="hackernews"),
            _item("news:hn-2", "news", "HN 2", 9.8, source="hackernews"),
            _item("news:hn-3", "news", "HN 3", 9.7, source="hackernews"),
            _item(
                "news:weak-rss",
                "news",
                "Weak RSS",
                5.4,
                source="rss",
                metadata={"feed_name": "Weak Feed"},
            ),
        ],
        config,
    )

    assert [item.id for item in selected if item.type == "news"] == [
        "news:hn-1",
        "news:hn-2",
        "news:hn-3",
    ]


def test_unified_paper_selection_prefers_two_top_venues_and_one_arxiv_preprint() -> None:
    config = UnifiedDigestModeConfig(
        max_total_items=10,
        section_limits={"news": 1, "repo": 1, "paper": 3},
    )
    top_venue_one = _item(
        "paper:iclr",
        "paper",
        "ICLR Paper",
        9.9,
        source="openreview",
        metadata={"venue": "ICLR", "venue_year": 2026, "status": "accepted"},
    )
    top_venue_two = _item(
        "paper:neurips",
        "paper",
        "NeurIPS Paper",
        9.8,
        source="openreview",
        metadata={"venue": "NeurIPS", "venue_year": 2025, "status": "spotlight"},
    )
    extra_top_venue = _item(
        "paper:icml",
        "paper",
        "ICML Paper",
        9.7,
        source="openreview",
        metadata={"venue": "ICML", "venue_year": 2025, "status": "oral"},
    )
    arxiv_preprint = _item(
        "paper:arxiv",
        "paper",
        "High Potential Preprint",
        8.0,
        source="arxiv",
        metadata={"status": "preprint"},
    )

    selected = select_items(
        [
            _item("news:1", "news", "News", 7.0),
            _item("repo:1", "repo", "Repo", 7.0),
            extra_top_venue,
            top_venue_one,
            arxiv_preprint,
            top_venue_two,
        ],
        config,
    )

    assert [item.id for item in selected if item.type == "paper"] == [
        "paper:iclr",
        "paper:neurips",
        "paper:arxiv",
    ]
    selected_by_id = {item.id: item for item in selected}
    assert selected_by_id["paper:iclr"].metadata["quality_label"] == "top_venue"
    assert selected_by_id["paper:iclr"].metadata["selection_reason"] == "current top-venue paper"
    assert selected_by_id["paper:arxiv"].metadata["quality_label"] == "high_potential"
    assert selected_by_id["paper:arxiv"].metadata["selection_reason"] == "high-potential arXiv preprint"


def test_unified_repo_selection_prefers_two_established_and_one_high_potential_repo() -> None:
    config = UnifiedDigestModeConfig(
        max_total_items=10,
        section_limits={"news": 1, "repo": 3, "paper": 1},
    )
    outside_preferred_band = _item(
        "repo:huge",
        "repo",
        "Huge Repo",
        10.0,
        metadata={"stars": 180000, "updated_at": datetime(2026, 5, 25, tzinfo=timezone.utc)},
    )
    established_one = _item(
        "repo:80k",
        "repo",
        "80k Repo",
        9.8,
        metadata={"stars": 80000, "updated_at": datetime(2026, 5, 25, tzinfo=timezone.utc)},
    )
    established_two = _item(
        "repo:50k",
        "repo",
        "50k Repo",
        9.7,
        metadata={"stars": 50000, "updated_at": datetime(2026, 5, 25, tzinfo=timezone.utc)},
    )
    high_potential = _item(
        "repo:1k",
        "repo",
        "1k Repo",
        8.0,
        metadata={
            "stars": 1200,
            "created_at": datetime(2026, 1, 10, tzinfo=timezone.utc),
            "updated_at": datetime(2026, 5, 25, tzinfo=timezone.utc),
        },
    )
    old_small_repo = _item(
        "repo:old-1k",
        "repo",
        "Old 1k Repo",
        9.9,
        metadata={
            "stars": 1100,
            "created_at": datetime(2022, 1, 10, tzinfo=timezone.utc),
            "updated_at": datetime(2026, 5, 25, tzinfo=timezone.utc),
        },
    )

    selected = select_items(
        [
            _item("news:1", "news", "News", 7.0),
            _item("paper:1", "paper", "Paper", 7.0),
            outside_preferred_band,
            old_small_repo,
            high_potential,
            established_two,
            established_one,
        ],
        config,
    )

    assert [item.id for item in selected if item.type == "repo"] == [
        "repo:80k",
        "repo:50k",
        "repo:1k",
    ]
    selected_by_id = {item.id: item for item in selected}
    assert selected_by_id["repo:80k"].metadata["quality_label"] == "classic"
    assert selected_by_id["repo:80k"].metadata["selection_reason"] == "established current repository"
    assert selected_by_id["repo:1k"].metadata["quality_label"] == "high_potential"
    assert selected_by_id["repo:1k"].metadata["selection_reason"] == "new high-potential repository"


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


def test_unified_rendering_formats_paper_source_with_year_and_status() -> None:
    config = UnifiedDigestModeConfig(
        max_items_per_type=8,
        max_total_items=20,
        section_order=["paper", "repo", "news"],
    )
    paper = _item(
        "paper:icml",
        "paper",
        "Useful Paper",
        8.0,
        metadata={"venue": "ICML", "venue_year": 2026, "status": "oral"},
    ).model_copy(
        update={
            "summary": (
                "This paper shows a practical way to evaluate AI agents. "
                "It helps students see why the benchmark could matter in real products. "
                "This third sentence should not appear in the digest."
            )
        }
    )
    repo = _item("repo:1", "repo", "Repo", 7.0)
    news = _item("news:1", "news", "News", 6.0)
    context = StageContext(mode="unified_digest", run_id="test")

    summary = asyncio.run(UnifiedDigestSummarizer(config).summarize([paper, repo, news], context))
    rendered = asyncio.run(UnifiedDigestRenderer(config).render(summary, [paper, repo, news], context))

    assert "   - Source: ICML 2026 (Oral)" in summary
    assert (
        "   - Description: This paper shows a practical way to evaluate AI agents. "
        "It helps students see why the benchmark could matter in real products."
    ) in summary
    assert "This third sentence should not appear" not in summary
    assert "Venue/status:" not in summary
    assert "Learn:" not in summary
    assert "ICML 2026 (Oral)" in str(rendered.metadata["web_html"])


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
        raw_content="Useful repo content",
    )
    paper = _item("paper:1", "paper", "Paper", 8.0)
    news = _item("news:1", "news", "News", 7.0)
    context = StageContext(mode="unified_digest", run_id="test")

    summary = asyncio.run(UnifiedDigestSummarizer(config).summarize([repo, paper, news], context))
    rendered = asyncio.run(UnifiedDigestRenderer(config).render(summary, [repo, paper, news], context))

    assert "### Repo to Study" not in summary
    assert "## GitHub Repos" in summary
    assert "/10" not in summary
    assert "   - 6k stars | 420 forks" in summary
    assert "open issues" not in summary
    assert "concrete learning evidence" not in summary
    assert "   - Description: Useful repo content" in summary
    assert "   - Tags: # Python, # agents, # mcp" in summary
    assert "   - Why:" not in summary
    assert "   - Study:" not in summary
    assert "license" not in summary
    assert "homepage" not in summary
    assert "   - Evidence:" not in summary
    assert "   - Files:" not in summary
    assert "# Python" in summary
    assert "# agents" in summary
    assert "   - Watch:" not in summary
    assert "## Research Papers" in summary
    assert "## Tech News" in summary
    assert "Evidence:" not in str(rendered.metadata["web_html"])
    assert "concrete learning evidence" not in str(rendered.metadata["web_html"])
    assert "/10" not in str(rendered.metadata["web_html"])
    assert "aurora-score" not in str(rendered.metadata["web_html"])
    assert "Files:" not in str(rendered.metadata["web_html"])
    assert "MIT license" not in str(rendered.metadata["web_html"])
    assert "homepage" not in str(rendered.metadata["web_html"])
    assert "Python" in str(rendered.metadata["web_html"])
    assert "agents" in str(rendered.metadata["web_html"])
    assert "<b>Why:</b>" not in str(rendered.metadata["web_html"])
    assert "<b>Value:</b>" not in str(rendered.metadata["web_html"])
    assert "Useful repo content" in str(rendered.metadata["web_html"])
    assert "<b>Study:</b>" not in str(rendered.metadata["web_html"])
    assert "Watch:" not in str(rendered.metadata["web_html"])


def test_unified_repo_cards_use_compact_stats_description_and_core_tags() -> None:
    config = UnifiedDigestModeConfig(
        max_items_per_type=8,
        max_total_items=20,
        section_order=["repo", "paper", "news"],
    )
    repo = _item(
        "repo:org/agent-kit",
        "repo",
        "org/agent-kit",
        9.0,
        url="https://github.com/org/agent-kit",
        metadata={
            "full_name": "org/agent-kit",
            "stars": 133_400,
            "forks": 18_300,
            "open_issues": 77,
            "language": "Python",
            "topics": ["ai-agent", "ai-coding-assistant", "workflow"],
            "description": "The open source coding agent.",
        },
        why_it_matters="LLM value should not be needed for the compact repo card.",
        raw_content="The open source coding agent.",
    )

    summary = asyncio.run(
        UnifiedDigestSummarizer(config).summarize(
            [repo],
            StageContext(mode="unified_digest", run_id="test"),
        )
    )
    rendered = asyncio.run(
        UnifiedDigestRenderer(config).render(
            summary,
            [repo],
            StageContext(mode="unified_digest", run_id="test"),
        )
    )
    web_html = str(rendered.metadata["web_html"])

    assert "133.4k stars | 18.3k forks" in summary
    assert "open issues" not in summary
    assert "Description: The open source coding agent." in summary
    assert "Tags: # Python, # ai agent, # ai coding assistant" in summary
    assert "Value:" not in summary
    assert "LLM value should not be needed" not in summary
    assert "133.4k stars | 18.3k forks" in web_html
    assert "77 open issues" not in web_html
    assert "The open source coding agent." in web_html
    assert "# Python" in web_html
    assert "# ai agent" in web_html
    assert "# ai coding assistant" in web_html
    assert "<b>Value:</b>" not in web_html


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


def test_unified_rendering_uses_polished_news_summary_fallback() -> None:
    config = UnifiedDigestModeConfig(max_items_per_type=8, max_total_items=20)
    item = _item(
        "news:release",
        "news",
        "vllm-project/vllm v0.23.0",
        9.0,
        source="github_releases",
        why_it_matters="",
        raw_content=(
            "Release v0.23.0 adds faster inference paths, benchmark updates, "
            "and compatibility fixes for production serving."
        ),
    )

    summary = asyncio.run(
        UnifiedDigestSummarizer(config).summarize(
            [item],
            StageContext(mode="unified_digest", run_id="test"),
        )
    )

    assert "   - Source: GitHub Releases" in summary
    assert "The release highlights" in summary
    assert "updates #" not in summary
    assert "faster inference paths" in summary
    assert "github_releases" not in summary
    assert "flagged this" not in summary


def test_unified_paper_description_hides_generic_scholar_fallback() -> None:
    config = UnifiedDigestModeConfig(
        max_items_per_type=8,
        max_total_items=20,
        section_order=["paper", "repo", "news"],
    )
    paper = _item(
        "paper:generic",
        "paper",
        "Reward Modeling for Multi-Agent Orchestration",
        9.0,
        source="arxiv",
        raw_content="",
        why_it_matters="Relevant ML research candidate for today's scholar radar.",
    )

    summary = asyncio.run(
        UnifiedDigestSummarizer(config).summarize(
            [paper],
            StageContext(mode="unified_digest", run_id="test"),
        )
    )

    assert "Relevant ML research candidate" not in summary
    assert (
        "This paper studies Reward Modeling for Multi-Agent Orchestration and why it may matter"
        in summary
    )


def test_public_copy_quality_rejects_visible_digest_slop() -> None:
    checks = [
        _item(
            "news:release",
            "news",
            "vllm-project/vllm v0.23.0",
            9.0,
            source="github_releases",
            summary="vllm-project/vllm v0.23.0 updates # vLLM v0.23.0 Release Notes.",
        ),
        _item(
            "news:rss",
            "news",
            "Why AI has not replaced software engineers",
            9.0,
            source="rss",
            metadata={"feed_name": "Simon Willison"},
            summary=(
                "Simon Willison covers Why AI has not replaced software engineers, "
                "with why AI has not replaced software engineers."
            ),
        ),
        _item(
            "repo:deterministic",
            "repo",
            "org/repo",
            9.0,
            why_it_matters=(
                "org/repo is worth studying because it has concrete learning evidence: "
                "57.4k stars, active recently, MIT license."
            ),
        ),
        _item(
            "paper:generic",
            "paper",
            "Reward Modeling for Agents",
            9.0,
            why_it_matters="Relevant ML research candidate for today's scholar radar.",
        ),
        _item(
            "paper:abstract",
            "paper",
            "Adaptive Streaming Reasoning",
            9.0,
            why_it_matters="",
            raw_content=(
                "Large reasoning models typically follow a read-then-think paradigm: "
                "they observe the complete input, reason over a static context, and then..."
            ),
        ),
        _item(
            "news:title-duplicate",
            "news",
            "The Fable 5 Export Controls Harm US Cyber Defense",
            9.0,
            summary=(
                "The Fable 5 Export Controls Harm US Cyber Defense: "
                "the Fable 5 Export Controls Harm US Cyber Defense quoted a security researcher."
            ),
        ),
        _item(
            "news:dangling",
            "news",
            "Build context-rich research agents",
            9.0,
            summary=(
                "Deep Agents and Bedrock AgentCore: this walkthrough targets developers "
                "building multi-step AI workflows who need."
            ),
        ),
        _item(
            "news:release-notes",
            "news",
            "vllm-project/vllm v0.23.0",
            9.0,
            source="github_releases",
            summary=(
                "vllm-project/vllm v0.23.0 release: vLLM v0.23.0 Release Notes "
                "* **DeepSeek-V4 matures across backends**."
            ),
        ),
        _item(
            "news:june18-similar",
            "news",
            "GLM-5.2 - Simon Willison",
            9.0,
            source="rss",
            metadata={"feed_name": "Simon Willison"},
            summary="GLM-5.2 - Simon Willison: GLM-5.2 is a new reasoning model. Similar i.",
        ),
        _item(
            "news:june18-and",
            "news",
            "Context intelligence in AWS Developer tools",
            9.0,
            source="rss",
            metadata={"feed_name": "AWS Machine Learning Blog"},
            summary="Context intelligence in AWS Developer tools: AWS describes context intelligence for developer tools and.",
        ),
        _item(
            "news:title-restatement",
            "news",
            "Introducing OpenAI Partner Network",
            9.0,
            source="rss",
            metadata={"feed_name": "OpenAI News"},
            summary="Introducing OpenAI Partner Network launches the OpenAI Partner Network.",
        ),
        _item(
            "paper:raw-voice",
            "paper",
            "TuneJury",
            9.0,
            source="arxiv",
            summary=(
                "We introduce TuneJury, an open, instance-level pairwise reward model "
                "for text-to-music that predicts a music preference score from a text prompt."
            ),
        ),
    ]

    failures = [public_copy_quality(item) for item in checks]

    assert all(not result.ok for result in failures)
    assert {reason for result in failures for reason in result.reasons} >= {
        "raw_markdown",
        "source_covers_template",
        "generic_scholar_fallback",
        "truncated_raw_abstract",
        "duplicated_title_prefix",
        "dangling_fragment",
        "release_note_remnant",
        "raw_abstract_voice",
        "title_restatement",
    }


def test_rendered_public_digest_audit_blocks_public_slop() -> None:
    audit = audit_rendered_public_digest(
        "# Aurora Unified Digest\n\n## Tech News\n\n"
        "Simon Willison covers Why AI has not replaced software engineers, with why AI has not replaced software engineers.\n\n"
        "## Source Health\n\nrun-20260618T052001Z\n",
        "<section>Release Notes</section>",
    )

    assert not audit.ok
    assert set(audit.reasons) >= {
        "source_covers_template",
        "release_note_remnant",
        "visible_diagnostics",
        "public_run_id",
    }


def test_public_copy_quality_uses_the_rendered_news_fallback() -> None:
    item = _item(
        "news:release",
        "news",
        "Example v1.2.3",
        9.0,
        source="github_releases",
        summary="Release Notes",
        raw_content="",
        why_it_matters="",
        learning_value="",
    )

    quality = public_copy_quality(item)

    assert not quality.ok
    assert quality.text.startswith("The release highlights")
    assert "deterministic_news_template" in quality.reasons


def test_rendered_public_digest_audit_blocks_deterministic_public_templates() -> None:
    audit = audit_rendered_public_digest(
        "# Aurora Unified Digest\n\n"
        "## Tech News\n\n"
        "1. [AWS update](https://example.com)\n"
        "   - Source: AWS Machine Learning Blog\n"
        "   - Summary: The update describes developers building for AR glasses face an infrastructure gap.\n\n"
        "## GitHub Repos\n\n"
        "1. [org/repo](https://github.com/org/repo)\n"
        "   - 12k stars | 800 forks | 10 open issues\n"
        "   - Value: org/repo is useful for studying how a real project organizes its architecture, examples, and developer workflow.\n\n"
        "## Research Papers\n\n"
        "1. [Paper](https://example.com/paper)\n"
        "   - Source: arxiv 2026 (Preprint)\n"
        "   - Description: This paper studies Paper and why the idea could matter for practical AI systems.\n",
        "",
    )

    assert not audit.ok
    assert set(audit.reasons) >= {
        "deterministic_news_template",
        "deterministic_repo_template",
        "deterministic_paper_template",
    }


def test_rendered_public_digest_audit_accepts_clean_digest() -> None:
    audit = audit_rendered_public_digest(
        "# Aurora Unified Digest\n\n"
        "## Tech News\n\n"
        "1. [Useful update](https://example.com)\n"
        "   - Source: OpenAI News\n"
        "   - Summary: The update explains a practical deployment change for AI teams.\n\n"
        "## GitHub Repos\n\n"
        "1. [org/repo](https://github.com/org/repo)\n"
        "   - 12k stars | 800 forks | 10 open issues\n"
        "   - Value: This repo is useful for learning a production agent workflow.\n\n"
        "## Research Papers\n\n"
        "1. [Paper](https://example.com/paper)\n"
        "   - Source: ICLR 2026 (Oral)\n"
        "   - Description: This paper explains a practical way to evaluate agents.\n",
        "<section><h2>Tech News</h2><p>The update explains a practical deployment change.</p></section>",
    )

    assert audit.ok


def test_unified_enrich_polishes_all_selected_public_copy(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    config = AuroraConfig(
        modes={
            "unified_digest": {
                "section_order": ["news", "repo", "paper"],
                "section_limits": {"news": 1, "repo": 1, "paper": 1},
            }
        }
    )
    items = [
        _item(
            "news:ok",
            "news",
            "Context-rich research agents",
            9.0,
            summary="AWS shows a practical pattern for context-rich research agents.",
        ),
        _item(
            "repo:ok",
            "repo",
            "org/agent-kit",
            9.0,
            metadata={"description": "A compact agent workflow toolkit."},
            why_it_matters="This repo teaches a useful agent workflow with clear examples.",
        ),
        _item(
            "paper:ok",
            "paper",
            "Agent Benchmark",
            9.0,
            summary="This paper explains a practical benchmark for testing AI agents.",
        ),
    ]
    context = StageContext(
        mode="unified_digest",
        run_id="test",
        config=config,
        metadata={"ai_usage": _empty_ai_usage()},
    )
    payloads = [
        _payload(summary="Polished news explains why the research-agent workflow matters for builders."),
        _payload(summary="This paper gives students a practical way to understand agent benchmark design."),
    ]

    enriched = asyncio.run(
        UnifiedEnrichStage(client=_FakeAIClient(payloads)).enrich(items, [], context)
    )
    summary = asyncio.run(
        UnifiedDigestSummarizer(config.modes.unified_digest).summarize(enriched, context)
    )

    assert "Polished news explains why the research-agent workflow matters for builders." in summary
    assert "A compact agent workflow toolkit." in summary
    assert "This paper gives students a practical way to understand agent benchmark design." in summary
    assert context.metadata["public_copy_quality"]["polished"] == 2
    assert context.metadata["public_copy_quality"]["repaired"] == 0


def test_unified_enrich_replaces_june_16_style_slop_after_failed_polish(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    config = AuroraConfig(
        modes={
            "unified_digest": {
                "section_order": ["news", "repo", "paper"],
                "section_limits": {"news": 1, "repo": 1, "paper": 1},
            }
        }
    )
    weak_news = _item(
        "news:weak-june16",
        "news",
        "Build context-rich research agents with Deep Agents and Bedrock AgentCore",
        10.0,
        summary=(
            "Deep Agents and Bedrock AgentCore: in this post, you'll build a competitive "
            "research agent that demonstrates this pattern end to end. This walkthrough "
            "targets developers building multi-step AI workflows who need."
        ),
        raw_content="",
    )
    replacement_news = _item(
        "news:replacement",
        "news",
        "Microsoft turns to AWS as GitHub faces AI capacity crunch",
        8.0,
        source="hackernews",
        summary="Microsoft is using AWS capacity to meet demand for GitHub AI features.",
    )
    items = [
        weak_news,
        replacement_news,
        _item("repo:1", "repo", "Repo", 8.0, why_it_matters="This repo teaches a useful workflow."),
        _item("paper:1", "paper", "Paper", 8.0, summary="This paper explains a practical agent benchmark."),
    ]
    context = StageContext(
        mode="unified_digest",
        run_id="test",
        config=config,
        metadata={"ai_usage": _empty_ai_usage()},
    )
    payloads = [
        _payload(summary="Deep Agents and Bedrock AgentCore: developers building workflows who need."),
        _payload(summary="Microsoft is using AWS capacity to meet demand for GitHub AI features."),
        _payload(why="This repo teaches a useful workflow for agent builders."),
        _payload(summary="This paper explains a practical agent benchmark for students."),
    ]

    enriched = asyncio.run(
        UnifiedEnrichStage(client=_FakeAIClient(payloads)).enrich(items, [], context)
    )
    summary = asyncio.run(
        UnifiedDigestSummarizer(config.modes.unified_digest).summarize(enriched, context)
    )

    assert "Microsoft turns to AWS" in summary
    assert "Build context-rich research agents" not in summary
    assert "who need." not in summary
    assert context.metadata["unified_selected_item_ids"][0] == "news:replacement"
    assert context.metadata["public_copy_quality"]["replacement_attempted"] >= 1
    assert context.metadata["public_copy_quality"]["replacement_succeeded"] == 1


def test_unified_enrich_does_not_sanitize_when_visible_llm_polish_fails(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    config = AuroraConfig(
        modes={
            "unified_digest": {
                "section_order": ["news", "repo", "paper"],
                "section_limits": {"news": 1, "repo": 1, "paper": 1},
            }
        }
    )
    weak_news = _item(
        "news:weak-june18",
        "news",
        "Context intelligence in AWS Developer tools",
        10.0,
        source="rss",
        metadata={"feed_name": "AWS Machine Learning Blog"},
        summary="Context intelligence in AWS Developer tools: AWS describes context intelligence and.",
        raw_content="AWS describes context intelligence features for developer tools.",
    )
    context = StageContext(
        mode="unified_digest",
        run_id="test",
        config=config,
        metadata={"ai_usage": _empty_ai_usage()},
    )

    enriched = asyncio.run(
        UnifiedEnrichStage(client=_FakeAIClient([_payload(summary="Context intelligence and.")])).enrich(
            [
                weak_news,
                _item("repo:1", "repo", "Repo", 8.0, why_it_matters="This repo teaches a useful workflow."),
                _item("paper:1", "paper", "Paper", 8.0, summary="This paper explains a practical agent benchmark."),
            ],
            [],
            context,
        )
    )
    summary = asyncio.run(UnifiedDigestSummarizer(config.modes.unified_digest).summarize(enriched, context))

    assert "Context intelligence and." not in summary
    assert "The update describes AWS describes context intelligence features" not in summary
    assert context.metadata["public_copy_quality"]["sanitized"] == 0
    assert context.metadata["public_copy_quality"]["failed"] >= 1


def test_unified_enrich_repairs_weak_selected_public_copy(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    config = AuroraConfig(
        modes={
            "unified_digest": {
                "section_order": ["news", "repo", "paper"],
                "section_limits": {"news": 1, "repo": 1, "paper": 1},
            }
        }
    )
    items = [
        _item(
            "news:weak",
            "news",
            "vllm-project/vllm v0.23.0",
            9.0,
            source="github_releases",
            summary="vllm-project/vllm v0.23.0 updates # vLLM v0.23.0 Release Notes.",
            raw_content="# vLLM v0.23.0 Release Notes\nThis release improves serving throughput.",
        ),
        _item(
            "repo:weak",
            "repo",
            "org/repo",
            9.0,
            metadata={"description": "A compact production AI tooling example."},
            why_it_matters="org/repo is worth studying because it has concrete learning evidence: 57k stars.",
        ),
        _item(
            "paper:weak",
            "paper",
            "Adaptive Streaming Reasoning",
            9.0,
            why_it_matters="",
            raw_content=(
                "Large reasoning models typically follow a read-then-think paradigm: "
                "they observe the complete input and then..."
            ),
        ),
    ]
    context = StageContext(
        mode="unified_digest",
        run_id="test",
        config=config,
        metadata={"ai_usage": _empty_ai_usage()},
    )

    enriched = asyncio.run(
        UnifiedEnrichStage(
            client=_FakeAIClient(
                [
                    _payload(
                        summary="Release notes highlight faster and more reliable vLLM serving for production inference."
                    ),
                    _payload(
                        summary=(
                            "This paper studies streaming reasoning for inputs that arrive over time. "
                            "It is useful for understanding agents that must react while context is still changing."
                        )
                    ),
                ]
            )
        ).enrich(items, [], context)
    )
    summary = asyncio.run(UnifiedDigestSummarizer(config.modes.unified_digest).summarize(enriched, context))

    assert "Release notes highlight faster and more reliable vLLM serving for production inference." in summary
    assert "A compact production AI tooling example." in summary
    assert "This paper studies streaming reasoning for inputs that arrive over time" in summary
    assert "updates #" not in summary
    assert "concrete learning evidence" not in summary
    assert context.metadata["unified_selected_item_ids"] == ["news:weak", "repo:weak", "paper:weak"]
    assert context.metadata["public_copy_quality"]["repaired"] == 2


def test_unified_enrich_replaces_item_when_repair_still_fails(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    config = AuroraConfig(
        modes={
            "unified_digest": {
                "section_order": ["news", "repo", "paper"],
                "section_limits": {"news": 1, "repo": 1, "paper": 1},
            }
        }
    )
    weak_news = _item(
        "news:weak",
        "news",
        "Weak News",
        10.0,
        source="rss",
        metadata={"feed_name": "Weak Feed"},
        summary="Weak Feed covers Weak News, with weak news.",
        raw_content="",
    )
    replacement_news = _item(
        "news:replacement",
        "news",
        "Replacement News",
        9.0,
        source="rss",
        metadata={"feed_name": "Replacement Feed"},
        summary="A new agent tooling release improves deployment workflows for developers.",
    )
    items = [
        weak_news,
        replacement_news,
        _item("repo:1", "repo", "Repo", 8.0, why_it_matters="This repo teaches a useful agent workflow."),
        _item("paper:1", "paper", "Paper", 8.0, summary="This paper explains a practical benchmark for AI agents."),
    ]
    context = StageContext(
        mode="unified_digest",
        run_id="test",
        config=config,
        metadata={"ai_usage": _empty_ai_usage()},
    )

    enriched = asyncio.run(
        UnifiedEnrichStage(client=_FakeAIClient([_weak_news_payload()])).enrich(items, [], context)
    )
    summary = asyncio.run(UnifiedDigestSummarizer(config.modes.unified_digest).summarize(enriched, context))

    assert "Replacement News" in summary
    assert "Weak News" not in summary
    assert context.metadata["unified_selected_item_ids"][0] == "news:replacement"
    assert context.metadata["public_copy_quality"]["replaced"] == 1


def test_unified_enrich_records_budget_skip_without_crashing() -> None:
    config = AuroraConfig(
        ai={"max_requests_per_run": 0, "fail_open_on_budget_exceeded": True},
        modes={
            "unified_digest": {
                "section_order": ["news", "repo", "paper"],
                "section_limits": {"news": 1, "repo": 1, "paper": 1},
            }
        },
    )
    items = [
        _item(
            "news:weak",
            "news",
            "Weak News",
            9.0,
            source="rss",
            metadata={"feed_name": "Weak Feed"},
            summary="Weak Feed covers Weak News, with weak news.",
        ),
        _item("repo:1", "repo", "Repo", 8.0),
        _item("paper:1", "paper", "Paper", 8.0),
    ]
    context = StageContext(
        mode="unified_digest",
        run_id="test",
        config=config,
        metadata={"ai_usage": _empty_ai_usage()},
    )

    enriched = asyncio.run(
        UnifiedEnrichStage(client=_FakeAIClient(_repair_payloads())).enrich(items, [], context)
    )

    assert [item.id for item in enriched] == ["news:weak", "repo:1", "paper:1"]
    assert context.metadata["ai_usage"]["skipped_by_budget"] >= 1
    assert context.metadata["public_copy_quality"]["failed"] >= 1


def test_unified_delivery_blocks_failed_public_audit_before_downstream() -> None:
    delivered: list[str] = []
    rendered = RenderedDigest(
        mode="unified_digest",
        title="Aurora Unified Digest",
        markdown="# Aurora Unified Digest\n\n## Tech News\n\nRelease Notes",
        metadata={"selected_item_ids": ["news:weak"], "recommended_repo_ids": []},
    )
    context = StageContext(mode="unified_digest", run_id="test")

    with pytest.raises(RuntimeError, match="public digest delivery blocked"):
        asyncio.run(
            UnifiedDeliveryStage(RepoLearningStateStore(Path("/tmp/aurora-test-state.json")), _Deliver(delivered)).deliver(
                rendered, context
            )
        )

    assert delivered == []
    assert context.metadata["public_copy_quality"]["delivery_blocked"] == 1


def test_unified_delivery_blocks_when_public_copy_quality_failed() -> None:
    delivered: list[str] = []
    rendered = RenderedDigest(
        mode="unified_digest",
        title="Aurora Unified Digest",
        markdown="# Aurora Unified Digest\n\n## Tech News\n\nClean-looking text.",
        metadata={"selected_item_ids": ["news:weak"], "recommended_repo_ids": []},
    )
    context = StageContext(
        mode="unified_digest",
        run_id="test",
        metadata={"public_copy_quality": {"failed": 1, "details": []}},
    )

    with pytest.raises(RuntimeError, match="public digest delivery blocked"):
        asyncio.run(
            UnifiedDeliveryStage(RepoLearningStateStore(Path("/tmp/aurora-test-state.json")), _Deliver(delivered)).deliver(
                rendered, context
            )
        )

    assert delivered == []
    assert context.metadata["public_copy_quality"]["delivery_blocked"] == 1


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
    assert "Study the evaluation setup." not in summary
    assert "Top News" in summary
    assert "Third News" in summary
    assert "Fourth News" in summary


def test_unified_paper_section_hides_learning_actions_and_semantic_scholar_link() -> None:
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
    assert "Learn:" not in section
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
    source: str = "test",
    metadata: dict | None = None,
    why_it_matters: str | None = None,
    learning_value: str | None = None,
    action_items: list[str] | None = None,
    raw_content: str | None = None,
    summary: str | None = None,
) -> SignalItem:
    return SignalItem(
        id=item_id,
        type=item_type,
        title=title,
        url=url or f"https://example.com/{item_id.replace(':', '-')}",
        source=source,
        published_at=datetime(2026, 5, 25, tzinfo=timezone.utc),
        raw_content=raw_content if raw_content is not None else f"{title} content",
        metadata=metadata or {},
        deterministic_score=score,
        final_score=score,
        summary=summary or "",
        why_it_matters=why_it_matters if why_it_matters is not None else f"{title} matters",
        learning_value=learning_value or "",
        action_items=action_items or [],
    )


def _empty_ai_usage() -> dict[str, int]:
    return {
        "requested_calls": 0,
        "succeeded_calls": 0,
        "failed_calls": 0,
        "skipped_by_budget": 0,
        "approx_prompt_tokens": 0,
        "approx_completion_tokens": 0,
        "approx_total_tokens": 0,
    }


def _repair_payloads() -> list[dict]:
    return [
        {
            "score": 8.0,
            "summary": "Release notes highlight faster and more reliable vLLM serving for production inference.",
            "why_it_matters": "",
            "learning_value": "",
            "action_items": [],
            "suggested_learning_path": "",
            "tags": [],
        },
        {
            "score": 8.0,
            "summary": "",
            "why_it_matters": (
                "org/repo is useful for learning how production AI tooling is structured "
                "and how its core workflow is organized."
            ),
            "learning_value": "",
            "action_items": [],
            "suggested_learning_path": "",
            "tags": [],
        },
        {
            "score": 8.0,
            "summary": (
                "This paper studies streaming reasoning for inputs that arrive over time. "
                "It is useful for understanding agents that must react while context is still changing."
            ),
            "why_it_matters": "",
            "learning_value": "",
            "action_items": [],
            "suggested_learning_path": "",
            "tags": [],
        },
    ]


def _weak_news_payload() -> dict:
    return {
        "score": 8.0,
        "summary": "Weak Feed covers Weak News, with weak news.",
        "why_it_matters": "",
        "learning_value": "",
        "action_items": [],
        "suggested_learning_path": "",
        "tags": [],
    }


def _payload(*, summary: str = "", why: str = "", learning: str = "") -> dict:
    return {
        "score": 8.0,
        "summary": summary,
        "why_it_matters": why,
        "learning_value": learning,
        "action_items": [],
        "suggested_learning_path": "",
        "tags": [],
    }


class _FakeAIClient:
    def __init__(self, payloads: list[dict]) -> None:
        self.payloads = list(payloads)

    def is_configured(self) -> bool:
        return True

    async def complete_json(self, system_prompt: str, user_prompt: str) -> dict:
        if not self.payloads:
            raise AssertionError("unexpected AI repair call")
        return self.payloads.pop(0)


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
