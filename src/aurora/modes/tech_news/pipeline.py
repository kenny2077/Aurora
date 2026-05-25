"""Pipeline factory for Aurora tech_news mode."""

from __future__ import annotations

import httpx

from aurora.ai import LLMRanker
from aurora.config import AuroraConfig
from aurora.delivery import ConfiguredDeliveryStage
from aurora.pipeline import ModePipeline
from aurora.modes.tech_news.render import TechNewsRenderer, TechNewsSummarizer
from aurora.modes.tech_news.scoring import TechNewsEnricher, TechNewsScorer
from aurora.modes.tech_news.sources import HackerNewsFetchStage, RSSFetchStage
from aurora.modes.tech_news.stages import TechNewsDeduplicateStage, TechNewsNormalizeStage


def build_tech_news_pipeline(
    config: AuroraConfig, http_client: httpx.AsyncClient | None = None
) -> ModePipeline:
    """Build the real tech_news MVP pipeline."""
    tech_news = config.modes.tech_news
    if not tech_news.enabled:
        raise ValueError("tech_news mode is disabled")
    llm_ranker = LLMRanker(
        config.ai,
        weights=config.pipeline.scoring.default_final_weights,
    )

    return ModePipeline(
        mode="tech_news",
        fetch_stages=[
            HackerNewsFetchStage(tech_news.sources.hackernews, http_client=http_client),
            RSSFetchStage(tech_news.sources.rss, http_client=http_client),
        ],
        normalize_stage=TechNewsNormalizeStage(),
        deduplicate_stage=TechNewsDeduplicateStage(),
        score_stage=TechNewsScorer(tech_news.filters, tech_news.scoring),
        enrich_stage=TechNewsEnricher(llm_ranker),
        summarize_stage=TechNewsSummarizer(),
        render_stage=TechNewsRenderer(),
        deliver_stage=ConfiguredDeliveryStage(config),
    )
