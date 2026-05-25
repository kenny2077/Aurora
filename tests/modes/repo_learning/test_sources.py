from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx
import pytest

from aurora.config import RepoLearningGitHubSearchConfig, RepoLearningModeConfig, RepoLearningSourcesConfig
from aurora.modes.repo_learning.github_client import (
    GitHubRepoClient,
    build_search_queries,
    validate_ref,
    validate_slug,
)
from aurora.modes.repo_learning.sources import GitHubSearchFetchStage
from aurora.pipeline import StageContext


def test_build_search_queries_include_presets_and_filters() -> None:
    config = RepoLearningGitHubSearchConfig(
        domains=["ai-agents"],
        min_stars=750,
        active_within_days=10,
        recent_years=2,
    )

    queries = build_search_queries(
        config,
        interests=["cv"],
        now=datetime(2026, 5, 25, tzinfo=timezone.utc),
    )

    assert [query.domain for query in queries] == ["cv", "agents"]
    assert "computer vision" in queries[0].query
    assert "language:Python" in queries[0].query
    assert "agent" in queries[1].query
    assert "language:TypeScript" in queries[1].query
    assert "stars:>=750" in queries[0].query
    assert "pushed:>=2026-05-15" in queries[0].query
    assert "created:>=" in queries[0].query


def test_build_search_queries_merges_custom_keywords_and_language_overrides() -> None:
    config = RepoLearningGitHubSearchConfig(
        domains=["workflow-automation"],
        custom_keywords=["graph rag"],
        languages=["Rust"],
    )

    queries = build_search_queries(
        config,
        interests=["ml"],
        now=datetime(2026, 5, 25, tzinfo=timezone.utc),
    )

    assert '"graph rag"' in queries[0].query
    assert "language:Rust" in queries[0].query
    assert "language:Python" not in queries[0].query


def test_slug_and_ref_validation() -> None:
    assert validate_slug("rtk-ai") == "rtk-ai"
    assert validate_ref("feature/mcp+agents") == "feature/mcp+agents"
    with pytest.raises(ValueError):
        validate_slug("../bad")
    with pytest.raises(ValueError):
        validate_ref("bad ref")


def test_github_client_uses_auth_and_parses_search_readme_and_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GH_SEARCH_TOKEN", "secret-token")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["Authorization"] == "Bearer secret-token"
        if request.url.path == "/search/repositories":
            return httpx.Response(200, json={"items": [_repo_payload()]})
        if request.url.host == "raw.githubusercontent.com":
            return httpx.Response(200, text="# Example\n\nReadme body")
        if request.url.path == "/repos/org/example/git/trees/main":
            return httpx.Response(200, json={"tree": [{"path": "pyproject.toml", "type": "blob"}]})
        return httpx.Response(404)

    async def exercise() -> tuple[list[dict], str | None, list[dict]]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            github = GitHubRepoClient(RepoLearningGitHubSearchConfig(), http_client=client)
            return (
                await github.search_repositories("agent"),
                await github.fetch_readme("org", "example", "main"),
                await github.fetch_tree("org", "example", "main"),
            )

    items, readme, tree = asyncio.run(exercise())

    assert items[0]["full_name"] == "org/example"
    assert readme == "# Example\n\nReadme body"
    assert tree == [{"path": "pyproject.toml", "type": "blob"}]
    assert [request.url.path for request in requests] == [
        "/search/repositories",
        "/org/example/main/README.md",
        "/repos/org/example/git/trees/main",
    ]


def test_github_client_returns_empty_context_on_404() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    async def exercise() -> tuple[str | None, list[dict]]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            github = GitHubRepoClient(RepoLearningGitHubSearchConfig(), http_client=client)
            return (
                await github.fetch_readme("org", "missing", "main"),
                await github.fetch_tree("org", "missing", "main"),
            )

    readme, tree = asyncio.run(exercise())

    assert readme is None
    assert tree == []


def test_github_search_fetch_stage_deduplicates_and_attaches_query_metadata() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": [_repo_payload(), _repo_payload()]})

    async def exercise() -> list[dict]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await GitHubSearchFetchStage(
                RepoLearningModeConfig(
                    sources=RepoLearningSourcesConfig(
                        github_search=RepoLearningGitHubSearchConfig(domains=["ai-agents"])
                    )
                ),
                http_client=client,
            ).fetch(
                StageContext(
                    mode="repo_learning",
                    run_id="test",
                    until=datetime(2026, 5, 25, tzinfo=timezone.utc),
                )
            )

    records = asyncio.run(exercise())

    assert len(records) == 1
    assert records[0]["aurora_source_domain"] == "agents"
    assert "stars:>=500" in records[0]["aurora_search_query"]


def _repo_payload() -> dict:
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
    }
