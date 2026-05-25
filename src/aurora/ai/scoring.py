"""Shared score-combining utilities."""

from __future__ import annotations

from aurora.config import FinalScoreWeights


def combine_scores(
    deterministic_score: float | None,
    llm_score: float | None,
    weights: FinalScoreWeights,
) -> float | None:
    """Combine deterministic and optional LLM scores into a bounded final score."""
    if deterministic_score is None and llm_score is None:
        return None
    if llm_score is None:
        return _bound(deterministic_score or 0.0)
    if deterministic_score is None:
        return _bound(llm_score)
    total_weight = weights.deterministic + weights.llm
    if total_weight <= 0:
        return _bound(deterministic_score)
    value = (deterministic_score * weights.deterministic + llm_score * weights.llm) / total_weight
    return _bound(value)


def _bound(value: float) -> float:
    return round(max(0.0, min(10.0, value)), 2)
