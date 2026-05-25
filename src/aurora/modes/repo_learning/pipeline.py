"""Pipeline factory for Aurora repo_learning mode."""

from __future__ import annotations

import httpx

from aurora.ai import LLMRanker
from aurora.config import AuroraConfig
from aurora.delivery import ConfiguredDeliveryStage
from aurora.modes.repo_learning.render import RepoLearningRenderer, RepoLearningSummarizer
from aurora.modes.repo_learning.scoring import RepoLearningEnricher, RepoLearningScorer
from aurora.modes.repo_learning.sources import GitHubSearchFetchStage
from aurora.modes.repo_learning.stages import (
    RepoLearningDeduplicateStage,
    RepoLearningDeliveryStage,
    RepoLearningNormalizeStage,
)
from aurora.modes.repo_learning.state import RepoLearningStateStore
from aurora.pipeline import ModePipeline


def build_repo_learning_pipeline(
    config: AuroraConfig, http_client: httpx.AsyncClient | None = None
) -> ModePipeline:
    """Build the real repo_learning MVP pipeline."""
    repo_learning = config.modes.repo_learning
    if not repo_learning.enabled:
        raise ValueError("repo_learning mode is disabled")

    state_store = RepoLearningStateStore(config.run.state_path)
    llm_ranker = LLMRanker(
        config.ai,
        weights=config.pipeline.scoring.default_final_weights,
    )
    return ModePipeline(
        mode="repo_learning",
        fetch_stages=[GitHubSearchFetchStage(repo_learning, http_client=http_client)],
        normalize_stage=RepoLearningNormalizeStage(),
        deduplicate_stage=RepoLearningDeduplicateStage(),
        score_stage=RepoLearningScorer(repo_learning, state_store=state_store),
        enrich_stage=RepoLearningEnricher(
            repo_learning,
            http_client=http_client,
            llm_ranker=llm_ranker,
        ),
        summarize_stage=RepoLearningSummarizer(repo_learning),
        render_stage=RepoLearningRenderer(repo_learning),
        deliver_stage=RepoLearningDeliveryStage(state_store, ConfiguredDeliveryStage(config)),
    )
