"""Pipeline factory for Aurora unified_digest mode."""

from __future__ import annotations

from collections.abc import Callable

from aurora.config import AuroraConfig
from aurora.modes.repo_learning import build_repo_learning_pipeline
from aurora.modes.scholar import build_scholar_pipeline
from aurora.modes.tech_news import build_tech_news_pipeline
from aurora.modes.unified_digest.render import UnifiedDigestRenderer, UnifiedDigestSummarizer
from aurora.modes.unified_digest.stages import (
    UnifiedDeduplicateStage,
    UnifiedDeliveryStage,
    UnifiedEnrichStage,
    UnifiedFetchStage,
    UnifiedNormalizeStage,
    UnifiedScoreStage,
)
from aurora.pipeline import ModePipeline


PipelineBuilder = Callable[[AuroraConfig], ModePipeline]


def build_unified_digest_pipeline(
    config: AuroraConfig,
    *,
    builders: dict[str, PipelineBuilder] | None = None,
) -> ModePipeline:
    """Build the unified digest pipeline."""
    unified = config.modes.unified_digest
    if not unified.enabled:
        raise ValueError("unified_digest mode is disabled")

    pipeline_builders = builders or {
        "tech_news": build_tech_news_pipeline,
        "scholar": build_scholar_pipeline,
        "repo_learning": build_repo_learning_pipeline,
    }
    return ModePipeline(
        mode="unified_digest",
        fetch_stages=[UnifiedFetchStage(config, pipeline_builders)],
        normalize_stage=UnifiedNormalizeStage(),
        deduplicate_stage=UnifiedDeduplicateStage(unified),
        score_stage=UnifiedScoreStage(),
        enrich_stage=UnifiedEnrichStage(),
        summarize_stage=UnifiedDigestSummarizer(unified),
        render_stage=UnifiedDigestRenderer(unified),
        deliver_stage=UnifiedDeliveryStage(),
    )
