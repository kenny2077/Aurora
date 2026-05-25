"""GitHub API helpers for repo_learning mode.

Portions adapted from Horizon-Github / RepoRadar (MIT). See NOTICE.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from aurora.config import RepoLearningGitHubSearchConfig


GITHUB_API_BASE_URL = "https://api.github.com"
RAW_GITHUB_BASE_URL = "https://raw.githubusercontent.com"
SLUG_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")
REF_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._/@+-]*$")

PRESET_QUERY_TERMS = {
    "ai-agents": ["agent", "llm", "tool use", "coding agent"],
    "mcp-ecosystem": ["mcp", "model context protocol", "mcp server"],
    "workflow-automation": ["workflow", "automation", "orchestration"],
}


@dataclass(frozen=True)
class RepoSearchQuery:
    """A concrete GitHub search query for one learning domain."""

    domain: str
    query: str


class GitHubRepoClient:
    """Small async GitHub client for search, README, and tree preview calls."""

    def __init__(
        self,
        config: RepoLearningGitHubSearchConfig,
        *,
        http_client: httpx.AsyncClient,
        api_base_url: str = GITHUB_API_BASE_URL,
        raw_base_url: str = RAW_GITHUB_BASE_URL,
    ) -> None:
        self.config = config
        self.http_client = http_client
        self.api_base_url = api_base_url.rstrip("/")
        self.raw_base_url = raw_base_url.rstrip("/")

    async def search_repositories(self, query: str) -> list[dict[str, Any]]:
        response = await self.http_client.get(
            f"{self.api_base_url}/search/repositories",
            params={
                "q": query,
                "sort": "stars",
                "order": "desc",
                "per_page": str(self.config.per_page),
            },
            headers=self.headers(),
        )
        response.raise_for_status()
        payload = response.json()
        items = payload.get("items", []) if isinstance(payload, dict) else []
        return [item for item in items if isinstance(item, dict)]

    async def fetch_readme(self, owner: str, repo: str, ref: str) -> str | None:
        owner = validate_slug(owner, label="owner")
        repo = validate_slug(repo, label="repo")
        ref = validate_ref(ref)
        response = await self.http_client.get(
            f"{self.raw_base_url}/{owner}/{repo}/{ref}/README.md",
            headers=self.headers(),
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.text

    async def fetch_tree(self, owner: str, repo: str, ref: str) -> list[dict[str, Any]]:
        owner = validate_slug(owner, label="owner")
        repo = validate_slug(repo, label="repo")
        ref = validate_ref(ref)
        response = await self.http_client.get(
            f"{self.api_base_url}/repos/{owner}/{repo}/git/trees/{ref}",
            params={"recursive": "1"},
            headers=self.headers(),
        )
        if response.status_code == 404:
            return []
        response.raise_for_status()
        payload = response.json()
        tree = payload.get("tree", []) if isinstance(payload, dict) else []
        return [entry for entry in tree if isinstance(entry, dict)]

    def headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "Aurora-RepoLearning/0.1",
        }
        token = _github_token(self.config.token_env)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers


def build_search_queries(
    config: RepoLearningGitHubSearchConfig,
    *,
    now: datetime | None = None,
) -> list[RepoSearchQuery]:
    """Build deterministic GitHub search queries from repo learning presets."""
    current = now or datetime.now(timezone.utc)
    active_after = (current - timedelta(days=config.active_within_days)).date().isoformat()
    created_after = (current - timedelta(days=config.recent_years * 365)).date().isoformat()

    queries: list[RepoSearchQuery] = []
    for domain in config.domains:
        terms = _terms_for_domain(domain)
        term_query = " OR ".join(_quote_search_term(term) for term in terms)
        pieces = [
            f"({term_query})",
            f"stars:>={config.min_stars}",
            f"pushed:>={active_after}",
        ]
        if config.recent_years:
            pieces.append(f"created:>={created_after}")
        queries.append(RepoSearchQuery(domain=domain, query=" ".join(pieces)))
    return queries


def validate_slug(value: str, *, label: str = "slug") -> str:
    cleaned = str(value).strip()
    if not SLUG_RE.fullmatch(cleaned):
        raise ValueError(f"invalid GitHub {label}: {value!r}")
    return cleaned


def validate_ref(value: str) -> str:
    cleaned = str(value).strip()
    if not REF_RE.fullmatch(cleaned):
        raise ValueError(f"invalid Git ref: {value!r}")
    return cleaned


def _github_token(token_env: str) -> str | None:
    token = os.getenv(token_env)
    if token:
        return token
    if token_env != "GITHUB_TOKEN":
        return os.getenv("GITHUB_TOKEN")
    return None


def _terms_for_domain(domain: str) -> list[str]:
    key = domain.strip().lower()
    if key in PRESET_QUERY_TERMS:
        return PRESET_QUERY_TERMS[key]
    return [part for part in key.replace("_", "-").split("-") if part] or [key]


def _quote_search_term(term: str) -> str:
    if " " in term:
        return f'"{term}"'
    return term
