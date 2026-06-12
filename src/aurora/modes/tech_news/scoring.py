"""Deterministic scoring and enrichment for tech_news mode."""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from datetime import datetime, timezone

from aurora.config import TechNewsFiltersConfig, TechNewsScoringConfig
from aurora.ai.ranker import LLMRanker
from aurora.modes.tech_news.notes import (
    build_tech_news_notes,
    ensure_polished_tech_news_notes,
)
from aurora.modes.tech_news.prompts import build_tech_news_prompt
from aurora.models import ScoreResult, SignalItem
from aurora.pipeline import StageContext


HIGH_AUTHORITY_RSS_FEEDS = {
    "aws machine learning blog": 7.4,
    "google ai blog": 7.6,
    "google deepmind blog": 8.0,
    "hugging face blog": 7.4,
    "nvidia ai blog": 7.3,
    "openai news": 8.0,
    "simon willison": 7.5,
}
HIGH_IMPACT_TERMS = {
    "benchmark",
    "dataset",
    "deployment",
    "developer",
    "evaluation",
    "framework",
    "inference",
    "open source",
    "production",
    "reasoning",
    "release",
    "research",
    "safety",
    "security",
    "tool",
}


class TechNewsScorer:
    """Score tech news items without LLM calls."""

    def __init__(self, filters: TechNewsFiltersConfig, scoring: TechNewsScoringConfig) -> None:
        self.filters = filters
        self.scoring = scoring

    async def score(self, items: Sequence[SignalItem], context: StageContext) -> list[ScoreResult]:
        return [self._score_item(item, context) for item in items]

    def _score_item(self, item: SignalItem, context: StageContext) -> ScoreResult:
        matched_keywords = _matched_keywords(item, self.filters.include_keywords)
        if _matched_keywords(item, self.filters.exclude_keywords):
            return ScoreResult(
                item_id=item.id,
                deterministic_score=0.0,
                final_score=0.0,
                score_breakdown={"excluded": 0.0},
                reason="excluded by keyword",
            )

        breakdown = {
            "source_authority": _source_authority(item),
            "engagement": _engagement(item),
            "recency": _recency(item, context),
            "topic_relevance": _topic_relevance(matched_keywords),
        }
        weights = {
            "source_authority": self.scoring.source_authority_weight,
            "engagement": self.scoring.engagement_weight,
            "recency": self.scoring.recency_weight,
            "topic_relevance": self.scoring.topic_relevance_weight,
        }
        total_weight = sum(weights.values()) or 1.0
        final_score = sum(breakdown[key] * weights[key] for key in breakdown) / total_weight
        final_score = round(max(0.0, min(10.0, final_score)), 2)
        if final_score < self.filters.min_source_score:
            final_score = 0.0
        return ScoreResult(
            item_id=item.id,
            deterministic_score=final_score,
            final_score=final_score,
            score_breakdown=breakdown,
            reason="deterministic tech_news score",
            tags=[item.source, *matched_keywords],
        )


class TechNewsEnricher:
    """Apply score results to SignalItem fields."""

    def __init__(self, llm_ranker: LLMRanker | None = None, *, llm_analysis_top_n: int = 12) -> None:
        self.llm_ranker = llm_ranker
        self.llm_analysis_top_n = max(0, llm_analysis_top_n)

    async def enrich(
        self,
        items: Sequence[SignalItem],
        score_results: Sequence[ScoreResult],
        context: StageContext,
    ) -> list[SignalItem]:
        scores_by_id = {score.item_id: score for score in score_results}
        enriched: list[SignalItem] = []
        for item in items:
            score = scores_by_id.get(item.id)
            if score is None:
                enriched.append(item)
                continue
            metadata = dict(item.metadata)
            metadata["score_breakdown"] = score.score_breakdown
            metadata["score_reason"] = score.reason
            notes = build_tech_news_notes(item)
            enriched.append(
                item.model_copy(
                    update={
                        "deterministic_score": score.deterministic_score,
                        "final_score": score.final_score,
                        "tags": score.tags,
                        "why_it_matters": notes.why_it_matters,
                        "learning_value": notes.learning_value,
                        "action_items": score.action_items or notes.action_items,
                        "metadata": metadata,
                    }
                )
            )
        if self.llm_ranker is None or self.llm_analysis_top_n == 0:
            return enriched
        context.metadata["llm_analysis_candidate_pool_count"] = len(enriched)
        candidates = sorted(enriched, key=_item_score, reverse=True)[: self.llm_analysis_top_n]
        analyses = await self.llm_ranker.analyze_items(candidates, build_tech_news_prompt, context)
        analyzed: list[SignalItem] = []
        for item in enriched:
            analysis = analyses.get(item.id)
            updated = self.llm_ranker.apply_analysis(item, analysis)
            analyzed.append(ensure_polished_tech_news_notes(updated))
        return analyzed


def _source_authority(item: SignalItem) -> float:
    if item.source == "hackernews":
        return 8.0
    if item.source == "rss":
        feed_name = str(item.metadata.get("feed_name") or "").strip().lower()
        return HIGH_AUTHORITY_RSS_FEEDS.get(feed_name, 6.5)
    return 5.0


def _engagement(item: SignalItem) -> float:
    if item.source == "hackernews":
        score = float(item.metadata.get("score") or 0)
        comments = float(item.metadata.get("descendants") or 0)
        return min(10.0, 2.0 + math.log10(score + 1) * 2.2 + math.log10(comments + 1) * 1.2)
    text = f"{item.title} {item.raw_content}".lower()
    content_signal = 0.8 if len(item.raw_content.split()) >= 40 else 0.0
    impact_signal = min(1.5, sum(1 for term in HIGH_IMPACT_TERMS if term in text) * 0.3)
    source_signal = max(0.0, (_source_authority(item) - 6.5) * 0.6)
    return min(7.0, 4.0 + content_signal + impact_signal + source_signal)


def _recency(item: SignalItem, context: StageContext) -> float:
    timestamp = item.published_at or item.updated_at
    if timestamp is None:
        return 0.0
    now = context.until or datetime.now(timezone.utc)
    age_hours = max(0.0, (now - timestamp).total_seconds() / 3600)
    if age_hours <= 6:
        return 10.0
    if age_hours <= 24:
        return 8.0
    if age_hours <= 72:
        return 5.5
    return 3.0


def _topic_relevance(matches: list[str]) -> float:
    if not matches:
        return 4.0
    return min(10.0, 5.0 + len(matches) * 1.5)


def _matched_keywords(item: SignalItem, keywords: list[str]) -> list[str]:
    text = f"{item.title} {item.raw_content}".lower()
    matches: list[str] = []
    for keyword in keywords:
        if " " in keyword or len(keyword) > 3:
            matched = keyword in text
        else:
            matched = re.search(rf"\b{re.escape(keyword)}\b", text) is not None
        if matched:
            matches.append(keyword)
    return matches


def _item_score(item: SignalItem) -> float:
    return item.final_score if item.final_score is not None else item.deterministic_score or 0.0
