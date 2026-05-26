"""GitHub search fetch stage for repo_learning mode."""

from __future__ import annotations

from typing import Any

import httpx

from aurora.config import RepoLearningModeConfig
from aurora.modes.repo_learning.github_client import GitHubRepoClient, build_search_queries
from aurora.pipeline import StageContext


class GitHubSearchFetchStage:
    """Fetch repository candidates from GitHub Search."""

    name = "github_search"

    def __init__(
        self,
        config: RepoLearningModeConfig,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self.http_client = http_client

    async def fetch(self, context: StageContext) -> list[dict[str, Any]]:
        if not self.config.sources.github_search.enabled:
            return []
        if self.http_client is not None:
            return await self._fetch_with_client(self.http_client, context)
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            return await self._fetch_with_client(client, context)

    async def _fetch_with_client(
        self, client: httpx.AsyncClient, context: StageContext
    ) -> list[dict[str, Any]]:
        github = GitHubRepoClient(self.config.sources.github_search, http_client=client)
        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        for search_query in build_search_queries(
            self.config.sources.github_search,
            interests=self.config.interests,
            now=context.until,
        ):
            try:
                candidates = await github.search_repositories(search_query.query)
            except httpx.HTTPStatusError as exc:
                context.metadata.setdefault("repo_learning_search_failures", []).append(
                    {
                        "domain": search_query.domain,
                        "query": search_query.query,
                        "status_code": str(exc.response.status_code),
                        "error": str(exc),
                    }
                )
                continue
            for item in candidates:
                key = _repo_key(item)
                if not key or key in seen:
                    continue
                seen.add(key)
                record = dict(item)
                record["aurora_source_domain"] = search_query.domain
                record["aurora_search_query"] = search_query.query
                records.append(record)
        return records


def _repo_key(item: dict[str, Any]) -> str:
    full_name = str(item.get("full_name") or "").strip().lower()
    if full_name:
        return full_name
    github_id = item.get("id")
    return str(github_id) if github_id is not None else ""
