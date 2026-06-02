"""Deterministic scholar scoring and enrichment."""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from datetime import datetime
from typing import Any

import httpx

from aurora.ai.ranker import LLMRanker
from aurora.config import ScholarModeConfig
from aurora.modes.scholar.cache import load_scholar_cache, write_scholar_cache
from aurora.modes.scholar.fields import (
    expanded_arxiv_categories,
    expanded_keyword_allowlist,
    expanded_venue_allowlist,
    field_tags,
)
from aurora.models import ScoreResult, SignalItem
from aurora.modes.scholar.prompts import build_scholar_prompt
from aurora.modes.scholar.semantic_scholar import SemanticScholarClient
from aurora.pipeline import StageContext


WEIGHTS = {
    "venue_signal": 1.6,
    "novelty_signal": 1.2,
    "recency_signal": 1.4,
    "code_signal": 1.0,
    "citation_signal": 1.0,
    "topic_relevance_signal": 1.8,
    "learning_value_signal": 1.2,
    "source_diversity_signal": 0.8,
}
EVIDENCE_TERMS = {"ablation", "baseline", "benchmark", "dataset", "evaluation", "experiment", "method", "result"}


class ScholarScorer:
    """Score scholar papers without LLM calls."""

    def __init__(self, config: ScholarModeConfig) -> None:
        self.config = config

    async def score(self, items: Sequence[SignalItem], context: StageContext) -> list[ScoreResult]:
        return [self._score_item(item) for item in items]

    def _score_item(self, item: SignalItem) -> ScoreResult:
        if _has_blocklisted_term(item, self.config):
            return ScoreResult(
                item_id=item.id,
                deterministic_score=0.0,
                final_score=0.0,
                score_breakdown={**{key: 0.0 for key in WEIGHTS}, "blocklisted": 1.0},
                reason="blocked by scholar keyword",
            )

        breakdown = {
            "venue_signal": _venue_signal(item, self.config),
            "novelty_signal": _novelty_signal(item, self.config),
            "recency_signal": _recency_signal(item, self.config),
            "code_signal": _code_signal(item),
            "citation_signal": _citation_signal(item),
            "topic_relevance_signal": _topic_relevance_signal(item, self.config),
            "learning_value_signal": _learning_value_signal(item),
            "source_diversity_signal": _source_diversity_signal(item),
        }
        score = round(max(0.0, min(10.0, sum(breakdown[key] * WEIGHTS[key] for key in WEIGHTS))), 2)
        return ScoreResult(
            item_id=item.id,
            deterministic_score=score,
            final_score=score,
            score_breakdown={key: round(value, 3) for key, value in breakdown.items()},
            reason="deterministic scholar score",
            tags=_score_tags(item, self.config),
        )


class ScholarEnricher:
    """Apply scholar score results to SignalItem fields."""

    def __init__(
        self,
        config: ScholarModeConfig | None = None,
        llm_ranker: LLMRanker | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self.llm_ranker = llm_ranker
        self.http_client = http_client

    async def enrich(
        self,
        items: Sequence[SignalItem],
        score_results: Sequence[ScoreResult],
        context: StageContext,
    ) -> list[SignalItem]:
        if not items and self.config is not None and self.config.fallback_cache_enabled:
            cached = load_scholar_cache(self.config, context)
            if cached:
                context.metadata["scholar_cached_fallback_used"] = True
            return cached

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
            enriched.append(
                item.model_copy(
                    update={
                        "deterministic_score": score.deterministic_score,
                        "final_score": score.final_score,
                        "tags": score.tags,
                        "metadata": metadata,
                        "why_it_matters": _why_it_matters(item),
                        "learning_value": _learning_value(item),
                    }
                )
            )
        if self.llm_ranker is not None:
            analyses = await self.llm_ranker.analyze_items(enriched, build_scholar_prompt, context)
            enriched = [self.llm_ranker.apply_analysis(item, analyses.get(item.id)) for item in enriched]
        if self.config is not None:
            enriched = await self._semantic_scholar_enrich(enriched, context)
        if self.config is not None and self.config.fallback_cache_enabled:
            write_scholar_cache(enriched, context)
        return enriched

    async def _semantic_scholar_enrich(
        self, items: list[SignalItem], context: StageContext
    ) -> list[SignalItem]:
        if self.config is None:
            return items
        if self.http_client is not None:
            return await SemanticScholarClient(
                self.config.sources.semantic_scholar,
                http_client=self.http_client,
            ).enrich_items(items, context)
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            return await SemanticScholarClient(
                self.config.sources.semantic_scholar,
                http_client=client,
            ).enrich_items(items, context)


def _venue_signal(item: SignalItem, config: ScholarModeConfig) -> float:
    venue = _norm(item.metadata.get("venue") or "")
    if not venue:
        return 0.0
    allowlisted = any(_norm(allowed) in venue for allowed in expanded_venue_allowlist(config))
    if not allowlisted:
        return 0.25 if item.source == "openreview" else 0.0
    status = _norm(item.metadata.get("status") or "")
    if "oral" in status or "spotlight" in status:
        return 1.0
    if "accept" in status or "poster" in status:
        return 0.95
    if "workshop" in status:
        return 0.65
    if "submitted" in status or "under review" in status:
        return 0.45
    return 0.75


def _novelty_signal(item: SignalItem, config: ScholarModeConfig) -> float:
    year = _paper_year(item)
    if year is None or year < config.min_year:
        return 0.0
    status = _norm(item.metadata.get("status") or "")
    if item.source == "arxiv" or "preprint" in status:
        return 1.0 if year >= config.max_year else 0.75
    if "oral" in status or "spotlight" in status:
        return 0.9 if year >= config.max_year else 0.75
    if "accept" in status:
        return 0.8 if year >= config.max_year else 0.65
    if "submitted" in status:
        return 0.55 if year >= config.max_year else 0.45
    return 0.35 if year >= config.max_year else 0.25


def _recency_signal(item: SignalItem, config: ScholarModeConfig) -> float:
    year = _paper_year(item)
    if year is None or year < config.min_year:
        return 0.0
    if year >= config.max_year:
        return 1.0
    span = max(1, config.max_year - config.min_year)
    return _clamp(0.55 + 0.45 * ((year - config.min_year) / span))


def _code_signal(item: SignalItem) -> float:
    has_code = bool(item.metadata.get("code_urls"))
    has_project = bool(item.metadata.get("project_urls"))
    if has_code and has_project:
        return 1.0
    if has_code:
        return 0.7
    if has_project:
        return 0.4
    if _is_top_venue_accepted(item):
        return 0.3
    return 0.0


def _citation_signal(item: SignalItem) -> float:
    citations = max(0, int(item.metadata.get("citation_count") or 0))
    influential = max(0, int(item.metadata.get("influential_citation_count") or 0))
    return 0.6 * min(1.0, math.log1p(citations) / math.log1p(250)) + 0.4 * min(
        1.0, math.log1p(influential) / math.log1p(50)
    )


def _topic_relevance_signal(item: SignalItem, config: ScholarModeConfig) -> float:
    text = _scoring_text(item)
    keyword_allowlist = expanded_keyword_allowlist(config)
    keyword_matches = sum(1 for keyword in keyword_allowlist if _norm(keyword) in text)
    keyword_signal = min(1.0, keyword_matches / 2) if keyword_allowlist else 0.5
    category_signal = 0.35 if set(_norm(c) for c in item.metadata.get("categories") or []).intersection(
        _norm(c) for c in expanded_arxiv_categories(config)
    ) else 0.0
    venue_signal = 0.25 if _venue_signal(item, config) >= 0.75 else 0.0
    return _clamp(keyword_signal + category_signal + venue_signal)


def _learning_value_signal(item: SignalItem) -> float:
    words = re.findall(r"\w+", item.raw_content)
    if len(words) >= 80:
        length_signal = 0.8
    elif len(words) >= 35:
        length_signal = 0.6
    elif len(words) >= 15:
        length_signal = 0.35
    else:
        length_signal = 0.1
    evidence_signal = min(0.35, sum(1 for term in EVIDENCE_TERMS if term in _norm(item.raw_content)) * 0.07)
    return _clamp(length_signal + evidence_signal)


def _source_diversity_signal(item: SignalItem) -> float:
    source_ids = item.metadata.get("source_ids") if isinstance(item.metadata.get("source_ids"), dict) else {}
    count = len({item.source, *[key for key, value in source_ids.items() if value]})
    if count >= 3:
        return 1.0
    if count == 2:
        return 0.65
    if count == 1:
        return 0.3
    return 0.0


def _has_blocklisted_term(item: SignalItem, config: ScholarModeConfig) -> bool:
    text = _scoring_text(item)
    return any(_norm(keyword) in text for keyword in config.keyword_blocklist)


def _paper_year(item: SignalItem) -> int | None:
    years = [
        item.metadata.get("venue_year"),
        item.published_at.year if item.published_at else None,
        item.updated_at.year if item.updated_at else None,
    ]
    valid_years = [int(year) for year in years if year]
    return max(valid_years) if valid_years else None


def _is_top_venue_accepted(item: SignalItem) -> bool:
    venue = str(item.metadata.get("venue") or "").upper()
    status = _norm(item.metadata.get("status") or "")
    return venue in {"ICML", "NEURIPS", "ICLR", "AISTATS", "COLT", "UAI", "MLSYS", "TMLR"} and (
        "accept" in status or "oral" in status or "spotlight" in status
    )


def _score_tags(item: SignalItem, config: ScholarModeConfig | None = None) -> list[str]:
    tags = [item.source]
    tags.extend(str(category) for category in item.metadata.get("categories") or [])
    if config is not None:
        tags.extend(field_tags(config))
    venue = item.metadata.get("venue")
    if venue:
        tags.append(str(venue))
    return list(dict.fromkeys(tags))


def _why_it_matters(item: SignalItem) -> str:
    venue = item.metadata.get("venue")
    if venue:
        return f"Relevant ML research candidate from {venue}."
    return "Relevant ML research candidate for today's scholar radar."


def _learning_value(item: SignalItem) -> str:
    if item.metadata.get("code_urls"):
        return "Read the method and inspect the linked implementation."
    return "Read the abstract and method sections to understand the contribution."


def _scoring_text(item: SignalItem) -> str:
    parts: list[str] = [
        item.title,
        item.raw_content,
        str(item.metadata.get("venue") or ""),
        str(item.metadata.get("status") or ""),
    ]
    parts.extend(str(value) for value in item.metadata.get("categories") or [])
    return _norm(" ".join(parts))


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value).lower()).strip()


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
