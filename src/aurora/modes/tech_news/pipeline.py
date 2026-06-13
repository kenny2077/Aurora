"""Pipeline factory for Aurora tech_news mode."""

from __future__ import annotations

import httpx

from aurora.ai import LLMRanker
from aurora.config import AuroraConfig
from aurora.delivery import ConfiguredDeliveryStage
from aurora.pipeline import ModePipeline
from aurora.modes.tech_news.render import TechNewsRenderer, TechNewsSummarizer
from aurora.modes.tech_news.scoring import TechNewsEnricher, TechNewsScorer
from aurora.modes.tech_news.sources import (
    GitHubReleasesFetchStage,
    HackerNewsFetchStage,
    RSSFetchStage,
    RedditFetchStage,
    expanded_rss_sources,
)
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

    fetch_stages = [
        HackerNewsFetchStage(tech_news.sources.hackernews, http_client=http_client),
        RSSFetchStage(expanded_rss_sources(tech_news.sources), http_client=http_client),
    ]
    if tech_news.sources.reddit.enabled:
        fetch_stages.append(RedditFetchStage(tech_news.sources.reddit, http_client=http_client))
    if tech_news.sources.github_releases.enabled:
        fetch_stages.append(
            GitHubReleasesFetchStage(
                tech_news.sources.github_releases,
                http_client=http_client,
            )
        )

    return ModePipeline(
        mode="tech_news",
        fetch_stages=fetch_stages,
        normalize_stage=TechNewsNormalizeStage(),
        deduplicate_stage=TechNewsDeduplicateStage(),
        score_stage=TechNewsScorer(tech_news.filters, tech_news.scoring),
        enrich_stage=TechNewsEnricher(
            llm_ranker,
            llm_analysis_top_n=tech_news.llm_analysis_top_n,
        ),
        summarize_stage=TechNewsSummarizer(),
        render_stage=TechNewsRenderer(),
        deliver_stage=ConfiguredDeliveryStage(config),
    )
