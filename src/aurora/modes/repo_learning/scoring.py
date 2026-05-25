"""Deterministic scoring and enrichment for repo_learning mode.

Portions adapted from Horizon-Github / RepoRadar (MIT). See NOTICE.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from typing import Any

import httpx

from aurora.ai.ranker import LLMRanker
from aurora.config import RepoLearningModeConfig
from aurora.modes.repo_learning.github_client import GitHubRepoClient
from aurora.modes.repo_learning.prompts import build_repo_learning_prompt
from aurora.modes.repo_learning.state import RepoLearningStateStore
from aurora.models import ScoreResult, SignalItem
from aurora.pipeline import StageContext


KEYWORDS = {
    "agent",
    "agentic",
    "llm",
    "mcp",
    "model context protocol",
    "workflow",
    "automation",
    "cli",
    "developer tool",
    "coding agent",
    "tool use",
    "orchestration",
}
WEIGHTS = {
    "relevance": 0.25,
    "learning_value": 0.20,
    "architecture_clarity": 0.15,
    "recent_activity": 0.15,
    "novelty": 0.10,
    "documentation_quality": 0.10,
    "community_signal": 0.05,
}
PACKAGE_BASENAMES = {
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "setup.py",
    "setup.cfg",
    "cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "dockerfile",
    "docker-compose.yml",
    "makefile",
    "taskfile.yml",
}


class RepoLearningScorer:
    """Score GitHub repositories without LLM calls."""

    def __init__(
        self,
        config: RepoLearningModeConfig,
        *,
        state_store: RepoLearningStateStore,
    ) -> None:
        self.config = config
        self.state_store = state_store

    async def score(self, items: Sequence[SignalItem], context: StageContext) -> list[ScoreResult]:
        now = context.until or datetime.now(timezone.utc)
        cutoff = now - timedelta(days=self.config.ranking.history_lookback_days)
        recent_ids = self.state_store.recent_ids(cutoff)
        return [self._score_item(item, context, recent_ids) for item in items]

    def _score_item(
        self, item: SignalItem, context: StageContext, recent_ids: set[str]
    ) -> ScoreResult:
        breakdown = {
            "relevance": _relevance(item),
            "learning_value": _learning_value_signal(item),
            "architecture_clarity": _architecture_clarity(item),
            "recent_activity": _recent_activity(item, context),
            "novelty": _novelty(item, context, self.config),
            "documentation_quality": _documentation_quality(item),
            "community_signal": _community_signal(item),
        }
        base_score = sum(breakdown[key] * WEIGHTS[key] for key in WEIGHTS)
        recently_recommended = item.id in recent_ids
        penalty = 2.5 if recently_recommended else 0.0
        final_score = round(_clamp(base_score - penalty), 2)
        score_breakdown = {key: round(value, 3) for key, value in breakdown.items()}
        score_breakdown["recently_recommended_penalty"] = 10.0 if recently_recommended else 0.0
        return ScoreResult(
            item_id=item.id,
            deterministic_score=final_score,
            final_score=final_score,
            score_breakdown=score_breakdown,
            reason="deterministic repo_learning score",
            tags=_score_tags(item),
            action_items=_fallback_action_items(item.metadata),
        )


class RepoLearningEnricher:
    """Fetch README/tree details for top candidates and apply score results."""

    def __init__(
        self,
        config: RepoLearningModeConfig,
        *,
        http_client: httpx.AsyncClient | None = None,
        llm_ranker: LLMRanker | None = None,
    ) -> None:
        self.config = config
        self.http_client = http_client
        self.llm_ranker = llm_ranker

    async def enrich(
        self,
        items: Sequence[SignalItem],
        score_results: Sequence[ScoreResult],
        context: StageContext,
    ) -> list[SignalItem]:
        if self.http_client is not None:
            return await self._enrich_with_client(items, score_results, self.http_client)
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            return await self._enrich_with_client(items, score_results, client)

    async def _enrich_with_client(
        self,
        items: Sequence[SignalItem],
        score_results: Sequence[ScoreResult],
        client: httpx.AsyncClient,
    ) -> list[SignalItem]:
        scores_by_id = {score.item_id: score for score in score_results}
        top_ids = {
            score.item_id
            for score in sorted(
                score_results,
                key=lambda score: score.final_score or 0.0,
                reverse=True,
            )[: self.config.ranking.enrich_top_n]
        }
        github = GitHubRepoClient(self.config.sources.github_search, http_client=client)
        enriched: list[SignalItem] = []
        for item in items:
            score = scores_by_id.get(item.id)
            metadata = dict(item.metadata)
            readme_text: str | None = None
            tree_paths: list[str] = []
            if item.id in top_ids:
                readme_text, tree_paths = await _fetch_repo_context(github, metadata)
                if readme_text:
                    metadata["readme_excerpt"] = _excerpt(readme_text, limit=2400)
                if tree_paths:
                    metadata["tree_preview"] = tree_paths[:200]
                    metadata["package_files"] = extract_package_files(tree_paths)
            if score is not None:
                metadata["score_breakdown"] = score.score_breakdown
                metadata["score_reason"] = score.reason
                if score.score_breakdown.get("recently_recommended_penalty", 0.0) > 0:
                    metadata["recently_recommended"] = True

            raw_content = _raw_content(metadata, item.raw_content)
            enriched.append(
                item.model_copy(
                    update={
                        "raw_content": raw_content,
                        "deterministic_score": score.deterministic_score if score else None,
                        "final_score": score.final_score if score else None,
                        "tags": _merged_tags(item, score),
                        "metadata": metadata,
                        "why_it_matters": why_recommended(metadata),
                        "learning_value": what_to_study(metadata),
                        "action_items": repo_action_items(metadata),
                    }
                )
            )
        if self.llm_ranker is None:
            return enriched
        analyses = await self.llm_ranker.analyze_items(enriched, build_repo_learning_prompt, context)
        return [self.llm_ranker.apply_analysis(item, analyses.get(item.id)) for item in enriched]


def extract_package_files(paths: Sequence[str], *, limit: int = 30) -> list[str]:
    """Extract package, docs, and example files from a repository tree."""
    selected: list[str] = []
    seen: set[str] = set()
    for raw_path in paths:
        path = str(raw_path).strip().lstrip("/")
        if not path or path in seen:
            continue
        posix = PurePosixPath(path)
        basename = posix.name.lower()
        is_manifest = basename in PACKAGE_BASENAMES
        is_docs_or_example = path.startswith(("docs/", "examples/", "example/"))
        is_workflow = path.startswith(".github/workflows/") and path.endswith((".yml", ".yaml"))
        if not (is_manifest or is_docs_or_example or is_workflow):
            continue
        selected.append(path)
        seen.add(path)
        if len(selected) >= limit:
            break
    return selected


def why_recommended(metadata: dict[str, Any]) -> str:
    full_name = metadata.get("full_name") or "this repository"
    stars = int(metadata.get("stars") or 0)
    language = metadata.get("language") or "the project stack"
    topics = ", ".join((metadata.get("topics") or [])[:3])
    if topics:
        return f"{full_name} combines {language} implementation details with strong signals around {topics}."
    if stars:
        return f"{full_name} has strong community traction with {stars} stars and useful learning surface."
    return f"{full_name} is a relevant repository candidate for hands-on learning."


def what_to_study(metadata: dict[str, Any]) -> str:
    package_files = metadata.get("package_files") or []
    if package_files:
        return f"Study {', '.join(package_files[:3])} to map dependencies, entrypoints, and examples."
    if metadata.get("readme_excerpt"):
        return "Study the README to identify architecture, setup flow, and extension points."
    return "Study the repository description, topics, and project structure before cloning."


def repo_action_items(metadata: dict[str, Any]) -> list[str]:
    full_name = str(metadata.get("full_name") or "the repository")
    package_files = metadata.get("package_files") or []
    first_action = (
        f"Read package and example files: {', '.join(package_files[:5])}."
        if package_files
        else "Read the README and inspect the top-level project structure."
    )
    return [
        first_action,
        f"Clone {full_name} and run the smallest documented example in one day.",
        "In one week, build a small extension or integration around the core workflow.",
    ]


def _fetch_repo_context_error(metadata: dict[str, Any], error: Exception) -> None:
    metadata["repo_learning_enrichment_error"] = str(error)


async def _fetch_repo_context(
    github: GitHubRepoClient, metadata: dict[str, Any]
) -> tuple[str | None, list[str]]:
    owner = str(metadata.get("owner") or "")
    name = str(metadata.get("name") or "")
    ref = str(metadata.get("default_branch") or "main")
    try:
        readme = await github.fetch_readme(owner, name, ref)
        tree = await github.fetch_tree(owner, name, ref)
    except Exception as exc:
        _fetch_repo_context_error(metadata, exc)
        return None, []
    return readme, [str(entry.get("path") or "") for entry in tree if entry.get("type") == "blob"]


def _relevance(item: SignalItem) -> float:
    matches = _matched_keywords(item)
    topic_matches = [topic for topic in item.metadata.get("topics") or [] if _norm(topic) in KEYWORDS]
    return _clamp(3.5 + len(matches) * 1.2 + len(topic_matches) * 0.8)


def _learning_value_signal(item: SignalItem) -> float:
    description_length = len(item.raw_content.split())
    topic_count = len(item.metadata.get("topics") or [])
    language_bonus = 1.0 if item.metadata.get("language") else 0.0
    docs_hint = 1.0 if item.metadata.get("homepage") else 0.0
    return _clamp(3.0 + min(2.0, description_length / 12) + min(2.0, topic_count * 0.4) + language_bonus + docs_hint)


def _architecture_clarity(item: SignalItem) -> float:
    score = 4.0
    if item.metadata.get("language"):
        score += 1.5
    if item.metadata.get("license"):
        score += 1.0
    if item.raw_content:
        score += 1.0
    if item.metadata.get("topics"):
        score += 1.0
    return _clamp(score)


def _recent_activity(item: SignalItem, context: StageContext) -> float:
    pushed_at = item.updated_at
    if pushed_at is None:
        return 0.0
    now = context.until or datetime.now(timezone.utc)
    age_days = max(0.0, (now - pushed_at).total_seconds() / 86400)
    if age_days <= 14:
        return 10.0
    if age_days <= 60:
        return 8.0
    if age_days <= 180:
        return 6.0
    if age_days <= 365:
        return 3.0
    return 1.0


def _novelty(item: SignalItem, context: StageContext, config: RepoLearningModeConfig) -> float:
    created_at = item.metadata.get("created_at")
    if not isinstance(created_at, datetime):
        return 5.0
    now = context.until or datetime.now(timezone.utc)
    age_days = max(0.0, (now - created_at).total_seconds() / 86400)
    recent_window_days = max(1, config.sources.github_search.recent_years * 365)
    if age_days <= recent_window_days / 4:
        return 10.0
    if age_days <= recent_window_days / 2:
        return 8.0
    if age_days <= recent_window_days:
        return 6.0
    return 3.0


def _documentation_quality(item: SignalItem) -> float:
    score = 3.0
    if len(item.raw_content.split()) >= 8:
        score += 2.0
    if item.metadata.get("homepage"):
        score += 1.5
    if item.metadata.get("license"):
        score += 1.0
    if item.metadata.get("topics"):
        score += 1.0
    return _clamp(score)


def _community_signal(item: SignalItem) -> float:
    stars = int(item.metadata.get("stars") or 0)
    forks = int(item.metadata.get("forks") or 0)
    open_issues = int(item.metadata.get("open_issues") or 0)
    score = 2.0 + math.log10(stars + 1) * 1.7 + math.log10(forks + 1) * 0.8
    if open_issues > 500:
        score -= 1.0
    return _clamp(score)


def _score_tags(item: SignalItem) -> list[str]:
    tags = [item.source]
    tags.extend(str(topic) for topic in item.metadata.get("topics") or [])
    language = item.metadata.get("language")
    if language:
        tags.append(str(language))
    tags.extend(_matched_keywords(item))
    return list(dict.fromkeys(tag for tag in tags if tag))


def _matched_keywords(item: SignalItem) -> list[str]:
    text = _scoring_text(item)
    return [keyword for keyword in sorted(KEYWORDS) if keyword in text]


def _scoring_text(item: SignalItem) -> str:
    parts = [item.title, item.raw_content]
    parts.extend(str(topic) for topic in item.metadata.get("topics") or [])
    return _norm(" ".join(parts))


def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", value.lower()).strip()


def _fallback_action_items(metadata: dict[str, Any]) -> list[str]:
    return repo_action_items(metadata)


def _raw_content(metadata: dict[str, Any], fallback: str) -> str:
    parts = [str(metadata.get("description") or fallback or "").strip()]
    if metadata.get("readme_excerpt"):
        parts.append(str(metadata["readme_excerpt"]))
    return "\n\n".join(part for part in parts if part)


def _merged_tags(item: SignalItem, score: ScoreResult | None) -> list[str]:
    tags = [*item.tags]
    if score is not None:
        tags.extend(score.tags)
    return list(dict.fromkeys(tag for tag in tags if tag))


def _excerpt(value: str, *, limit: int) -> str:
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _clamp(value: float) -> float:
    return max(0.0, min(10.0, value))
