from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from aurora.config import RepoLearningModeConfig, RepoLearningRankingConfig
from aurora.modes.repo_learning.render import RepoLearningRenderer, RepoLearningSummarizer
from aurora.modes.repo_learning.scoring import (
    RepoLearningEnricher,
    RepoLearningScorer,
    extract_package_files,
)
from aurora.modes.repo_learning.stages import (
    RepoLearningDeduplicateStage,
    RepoLearningDeliveryStage,
    RepoLearningNormalizeStage,
)
from aurora.modes.repo_learning.state import RepoLearningStateStore
from aurora.models import ScoreResult, SignalItem
from aurora.pipeline import StageContext


def _context() -> StageContext:
    return StageContext(
        mode="repo_learning",
        run_id="test",
        since=datetime(2026, 5, 24, tzinfo=timezone.utc),
        until=datetime(2026, 5, 25, tzinfo=timezone.utc),
    )


def test_normalize_converts_github_records_to_repo_signal_items() -> None:
    items = asyncio.run(RepoLearningNormalizeStage().normalize([_raw_repo()], _context()))

    assert items == [
        SignalItem(
            id="repo:org/example",
            type="repo",
            title="org/example",
            url="https://github.com/org/example",
            source="github_search",
            updated_at=datetime(2026, 5, 24, tzinfo=timezone.utc),
            raw_content="Agent workflow automation toolkit",
            metadata={
                "github_id": 1,
                "node_id": "node-1",
                "owner": "org",
                "name": "example",
                "full_name": "org/example",
                "description": "Agent workflow automation toolkit",
                "stars": 1000,
                "forks": 50,
                "watchers": 1000,
                "open_issues": 12,
                "language": "Python",
                "topics": ["agent", "workflow"],
                "default_branch": "main",
                "homepage": "https://example.dev",
                "license": "MIT",
                "source_domains": ["ai-agents"],
                "source_queries": ["agent stars:>=500"],
                "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
                "pushed_at": datetime(2026, 5, 24, tzinfo=timezone.utc),
            },
            tags=["agent", "workflow", "Python"],
        )
    ]


def test_deduplicate_collapses_github_identity_duplicates() -> None:
    first = _repo("repo:org/example", "org/example", {"github_id": 1, "node_id": "a"})
    second = _repo("repo:org/example-copy", "Org/Example", {"github_id": 1, "node_id": "b", "stars": 2000})
    third = _repo("repo:org/other", "org/other", {"github_id": 2, "node_id": "c"})

    deduped = asyncio.run(
        RepoLearningDeduplicateStage().deduplicate([first, second, third], _context())
    )

    assert [item.id for item in deduped] == ["repo:org/example-copy", "repo:org/other"]
    assert deduped[0].metadata["stars"] == 2000


def test_scoring_rewards_relevant_active_repos_and_suppresses_recent_state(tmp_path: Path) -> None:
    state_store = RepoLearningStateStore(tmp_path / "state.json")
    state_store.mark_recommended(
        ["repo:org/recent"],
        datetime(2026, 5, 24, tzinfo=timezone.utc),
    )
    strong = _repo(
        "repo:org/strong",
        "org/strong",
        {
            "stars": 5000,
            "forks": 300,
            "topics": ["agent", "mcp", "workflow"],
            "language": "Python",
            "license": "MIT",
            "homepage": "https://strong.dev",
            "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        },
        description="LLM agent workflow automation with model context protocol tools",
    )
    recent = strong.model_copy(update={"id": "repo:org/recent", "title": "org/recent"})

    strong_score, recent_score = asyncio.run(
        RepoLearningScorer(RepoLearningModeConfig(), state_store=state_store).score(
            [strong, recent],
            _context(),
        )
    )

    assert strong_score.final_score > recent_score.final_score
    assert recent_score.score_breakdown["recently_recommended_penalty"] == 10.0
    assert set(strong_score.score_breakdown) == {
        "relevance",
        "learning_value",
        "architecture_clarity",
        "recent_activity",
        "novelty",
        "documentation_quality",
        "community_signal",
        "practical_adoption",
        "recently_recommended_penalty",
    }
    assert strong_score.score_breakdown["practical_adoption"] > 5.0


def test_scoring_state_changes_next_run_order(tmp_path: Path) -> None:
    state_store = RepoLearningStateStore(tmp_path / "state.json")
    first = _repo("repo:org/first", "org/first", {"stars": 5000})
    second = _repo("repo:org/second", "org/second", {"stars": 4500})
    config = RepoLearningModeConfig()

    before = asyncio.run(
        RepoLearningScorer(config, state_store=state_store).score([first, second], _context())
    )
    state_store.mark_recommended(["repo:org/first"], datetime(2026, 5, 24, tzinfo=timezone.utc))
    after = asyncio.run(
        RepoLearningScorer(config, state_store=state_store).score([first, second], _context())
    )

    assert before[0].final_score >= before[1].final_score
    assert after[0].final_score < after[1].final_score


def test_extract_package_files_selects_manifests_docs_examples_and_workflows() -> None:
    files = extract_package_files(
        [
            "src/main.py",
            "pyproject.toml",
            "docs/getting-started.md",
            "examples/basic.py",
            ".github/workflows/ci.yml",
            "README.md",
        ]
    )

    assert files == [
        "pyproject.toml",
        "docs/getting-started.md",
        "examples/basic.py",
        ".github/workflows/ci.yml",
    ]


def test_enricher_fetches_readme_tree_and_fills_learning_fields() -> None:
    item = _repo(
        "repo:org/example",
        "org/example",
        {
            "owner": "org",
            "name": "example",
            "full_name": "org/example",
            "default_branch": "main",
            "stars": 1000,
            "topics": ["agent"],
            "language": "Python",
            "description": "Agent toolkit",
        },
    )
    score = ScoreResult(
        item_id=item.id,
        deterministic_score=8.0,
        final_score=8.0,
        score_breakdown={"relevance": 8.0},
        tags=["github_search", "agent"],
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "raw.githubusercontent.com":
            return httpx.Response(200, text="# Example\n\nRun the agent workflow.")
        return httpx.Response(
            200,
            json={
                "tree": [
                    {"path": "pyproject.toml", "type": "blob"},
                    {"path": "examples/run.py", "type": "blob"},
                    {"path": "src/ignored.py", "type": "blob"},
                ]
            },
        )

    async def exercise() -> list[SignalItem]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await RepoLearningEnricher(
                RepoLearningModeConfig(ranking=RepoLearningRankingConfig(enrich_top_n=1)),
                http_client=client,
            ).enrich([item], [score], _context())

    enriched = asyncio.run(exercise())[0]

    assert enriched.final_score == 8.0
    assert enriched.llm_score is None
    assert enriched.metadata["package_files"] == ["pyproject.toml", "examples/run.py"]
    assert "Run the agent workflow" in enriched.raw_content
    assert enriched.why_it_matters
    assert enriched.learning_value
    assert len(enriched.action_items) == 3


def test_enricher_explains_repo_with_evidence_and_specific_actions() -> None:
    item = _repo(
        "repo:org/evidence",
        "org/evidence",
        {
            "owner": "org",
            "name": "evidence",
            "full_name": "org/evidence",
            "default_branch": "main",
            "stars": 5200,
            "forks": 420,
            "open_issues": 35,
            "topics": ["agent", "workflow", "mcp"],
            "language": "Python",
            "license": "MIT",
            "homepage": "https://evidence.dev",
            "description": "Agent workflow toolkit",
        },
    )
    score = ScoreResult(
        item_id=item.id,
        deterministic_score=9.0,
        final_score=9.0,
        score_breakdown={
            "relevance": 9.0,
            "learning_value": 8.0,
            "architecture_clarity": 8.0,
            "documentation_quality": 8.0,
            "community_signal": 7.0,
        },
        tags=["github_search", "agent", "workflow"],
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "raw.githubusercontent.com":
            return httpx.Response(200, text="# Evidence\n\nRun examples/quickstart.py.")
        return httpx.Response(
            200,
            json={
                "tree": [
                    {"path": "pyproject.toml", "type": "blob"},
                    {"path": "docs/architecture.md", "type": "blob"},
                    {"path": "examples/quickstart.py", "type": "blob"},
                    {"path": ".github/workflows/ci.yml", "type": "blob"},
                ]
            },
        )

    async def exercise() -> SignalItem:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return (
                await RepoLearningEnricher(
                    RepoLearningModeConfig(ranking=RepoLearningRankingConfig(enrich_top_n=1)),
                    http_client=client,
                ).enrich([item], [score], _context())
            )[0]

    enriched = asyncio.run(exercise())

    assert "5.2k stars" in enriched.why_it_matters
    assert "README found" in enriched.why_it_matters
    assert "pyproject.toml" in enriched.learning_value
    assert "docs/architecture.md" in enriched.learning_value
    assert "examples/quickstart.py" in enriched.learning_value
    assert enriched.metadata["recommendation_evidence"][:3] == [
        "5.2k stars",
        "420 forks",
        "active recently",
    ]
    assert "README found" in enriched.metadata["recommendation_evidence"]
    assert "examples/quickstart.py" in enriched.metadata["recommendation_evidence"]
    assert enriched.metadata["quality_warnings"] == []
    assert any("pyproject.toml" in action for action in enriched.action_items)
    assert any("examples/quickstart.py" in action for action in enriched.action_items)


def test_enricher_flags_noisy_repo_quality_warnings() -> None:
    item = _repo(
        "repo:org/noisy",
        "org/noisy",
        {
            "owner": "org",
            "name": "noisy",
            "full_name": "org/noisy",
            "default_branch": "main",
            "stars": 6000,
            "forks": 20,
            "open_issues": 900,
            "topics": [],
            "language": None,
            "description": "",
        },
        description="",
    )
    score = ScoreResult(
        item_id=item.id,
        deterministic_score=8.5,
        final_score=8.5,
        score_breakdown={"community_signal": 8.0, "documentation_quality": 2.0},
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "raw.githubusercontent.com":
            return httpx.Response(404)
        return httpx.Response(200, json={"tree": []})

    async def exercise() -> SignalItem:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return (
                await RepoLearningEnricher(
                    RepoLearningModeConfig(ranking=RepoLearningRankingConfig(enrich_top_n=1)),
                    http_client=client,
                ).enrich([item], [score], _context())
            )[0]

    enriched = asyncio.run(exercise())

    assert enriched.metadata["quality_warnings"]
    assert "very high open issue count" in enriched.metadata["quality_warnings"]
    assert "README or package files were not found during enrichment" in enriched.metadata["quality_warnings"]
    assert "missing clear topics or language metadata" in enriched.metadata["quality_warnings"]
    assert "Watch:" not in enriched.why_it_matters
    assert "Read the README" in enriched.action_items[0]


def test_enricher_passes_stage_context_to_llm_ranker() -> None:
    context = _context()
    item = _repo(
        "repo:org/example",
        "org/example",
        {
            "owner": "org",
            "name": "example",
            "full_name": "org/example",
            "default_branch": "main",
        },
    )
    score = ScoreResult(
        item_id=item.id,
        deterministic_score=8.0,
        final_score=8.0,
        score_breakdown={"relevance": 8.0},
    )
    ranker = _RecordingRanker()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "raw.githubusercontent.com":
            return httpx.Response(200, text="# Example")
        return httpx.Response(200, json={"tree": []})

    async def exercise() -> list[SignalItem]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await RepoLearningEnricher(
                RepoLearningModeConfig(ranking=RepoLearningRankingConfig(enrich_top_n=1)),
                http_client=client,
                llm_ranker=ranker,
            ).enrich([item], [score], context)

    enriched = asyncio.run(exercise())

    assert enriched[0].id == item.id
    assert ranker.context is context


def test_rendering_is_score_ordered_capped_and_delivery_updates_state(tmp_path: Path) -> None:
    config = RepoLearningModeConfig(ranking=RepoLearningRankingConfig(final_item_count=1))
    state_store = RepoLearningStateStore(tmp_path / "state.json")
    low = _repo("repo:org/low", "org/low", {"stars": 10}).model_copy(
        update={"final_score": 3.0}
    )
    high = _repo("repo:org/high", "org/high", {"stars": 100}).model_copy(
        update={
            "final_score": 9.0,
            "why_it_matters": "High signal.",
            "learning_value": "Study package files.",
            "action_items": ["Read files."],
            "metadata": {
                "stars": 100,
                "forks": 7,
                "open_issues": 2,
                "language": "Python",
                "license": "MIT",
                "homepage": "https://high.example.com",
                "package_files": ["pyproject.toml"],
                "recommendation_evidence": ["README found"],
            },
        }
    )

    summary = asyncio.run(RepoLearningSummarizer(config).summarize([low, high], _context()))
    rendered = asyncio.run(RepoLearningRenderer(config).render(summary, [low, high], _context()))
    delivery = asyncio.run(RepoLearningDeliveryStage(state_store).deliver(rendered, _context()))
    recent = state_store.recent_ids(datetime(2026, 5, 25, tzinfo=timezone.utc) - timedelta(days=1))

    assert "Selected 1 GitHub repo(s)." in summary
    assert "org/high" in summary
    assert "org/low" not in summary
    assert "100 stars | 7 forks | 2 open issues" in summary
    assert "- Value: High signal." in summary
    assert "- Why:" not in summary
    assert "- Language:" not in summary
    assert "- Evidence:" not in summary
    assert "- Files:" not in summary
    assert "- Study:" not in summary
    assert "- Watch:" not in summary
    assert "- Actions:" not in summary
    assert "Read files." not in summary
    assert "MIT license" not in summary
    assert "homepage" not in summary
    assert rendered.metadata["recommended_repo_ids"] == ["repo:org/high"]
    assert rendered.html is not None
    assert "aurora-repo-card" in rendered.html
    assert rendered.metadata["web_html"]
    assert "org/high" in str(rendered.metadata["web_html"])
    assert "Files:" not in str(rendered.metadata["web_html"])
    assert "<b>Evidence:</b>" not in str(rendered.metadata["web_html"])
    assert "<b>Study:</b>" not in str(rendered.metadata["web_html"])
    assert "Watch:" not in str(rendered.metadata["web_html"])
    assert "Read files." not in str(rendered.metadata["web_html"])
    assert "<b>Value:</b> High signal." in str(rendered.metadata["web_html"])
    assert "MIT license" not in str(rendered.metadata["web_html"])
    assert "homepage" not in str(rendered.metadata["web_html"])
    assert delivery[0].metadata["recommended_count"] == 1
    assert recent == {"repo:org/high"}


def test_state_store_treats_corrupt_state_as_empty(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    state_path.write_text("not-json", encoding="utf-8")
    store = RepoLearningStateStore(state_path)

    assert store.recent_ids(datetime(2026, 5, 1, tzinfo=timezone.utc)) == set()
    store.mark_recommended(["repo:org/example"], datetime(2026, 5, 25, tzinfo=timezone.utc))
    assert store.recent_ids(datetime(2026, 5, 1, tzinfo=timezone.utc)) == {"repo:org/example"}


def _raw_repo() -> dict:
    return {
        "id": 1,
        "node_id": "node-1",
        "full_name": "org/example",
        "html_url": "https://github.com/org/example",
        "description": "Agent workflow automation toolkit",
        "stargazers_count": 1000,
        "forks_count": 50,
        "watchers_count": 1000,
        "open_issues_count": 12,
        "language": "Python",
        "topics": ["agent", "workflow"],
        "default_branch": "main",
        "homepage": "https://example.dev",
        "license": {"spdx_id": "MIT"},
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-05-20T00:00:00Z",
        "pushed_at": "2026-05-24T00:00:00Z",
        "aurora_source_domain": "ai-agents",
        "aurora_search_query": "agent stars:>=500",
    }


def _repo(
    item_id: str,
    full_name: str,
    metadata: dict,
    *,
    description: str = "Agent workflow automation toolkit",
) -> SignalItem:
    owner, name = full_name.split("/", 1)
    base_metadata = {
        "owner": owner,
        "name": name,
        "full_name": full_name,
        "description": description,
        "stars": 1000,
        "forks": 50,
        "open_issues": 10,
        "topics": ["agent", "workflow"],
        "language": "Python",
        "default_branch": "main",
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
    }
    base_metadata.update(metadata)
    return SignalItem(
        id=item_id,
        type="repo",
        title=full_name,
        url=f"https://github.com/{full_name}",
        source="github_search",
        updated_at=datetime(2026, 5, 24, tzinfo=timezone.utc),
        raw_content=description,
        metadata=base_metadata,
    )


class _RecordingRanker:
    def __init__(self) -> None:
        self.context: StageContext | None = None

    async def analyze_items(self, items, prompt_builder, context: StageContext) -> dict:
        self.context = context
        return {}

    def apply_analysis(self, item: SignalItem, analysis) -> SignalItem:
        return item
