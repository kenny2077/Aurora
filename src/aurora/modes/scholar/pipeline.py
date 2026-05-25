"""Pipeline factory for Aurora scholar mode."""

from __future__ import annotations

import httpx

from aurora.ai import LLMRanker
from aurora.config import AuroraConfig
from aurora.delivery import ConfiguredDeliveryStage
from aurora.modes.scholar.render import ScholarRenderer, ScholarSummarizer
from aurora.modes.scholar.scoring import ScholarEnricher, ScholarScorer
from aurora.modes.scholar.sources import ArxivFetchStage, OpenReviewFetchStage
from aurora.modes.scholar.stages import ScholarDeduplicateStage, ScholarNormalizeStage
from aurora.pipeline import ModePipeline


def build_scholar_pipeline(
    config: AuroraConfig, http_client: httpx.AsyncClient | None = None
) -> ModePipeline:
    """Build the real scholar MVP pipeline."""
    scholar = config.modes.scholar
    if not scholar.enabled:
        raise ValueError("scholar mode is disabled")
    llm_ranker = LLMRanker(
        config.ai,
        weights=config.pipeline.scoring.default_final_weights,
    )

    return ModePipeline(
        mode="scholar",
        fetch_stages=[
            ArxivFetchStage(scholar, http_client=http_client),
            OpenReviewFetchStage(scholar, http_client=http_client),
        ],
        normalize_stage=ScholarNormalizeStage(),
        deduplicate_stage=ScholarDeduplicateStage(),
        score_stage=ScholarScorer(scholar),
        enrich_stage=ScholarEnricher(llm_ranker),
        summarize_stage=ScholarSummarizer(scholar),
        render_stage=ScholarRenderer(),
        deliver_stage=ConfiguredDeliveryStage(config),
    )
