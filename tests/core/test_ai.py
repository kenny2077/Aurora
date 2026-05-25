from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from aurora.ai.json import parse_json_object
from aurora.ai.ranker import LLMAnalysis, LLMRanker
from aurora.ai.scoring import combine_scores
from aurora.config import AIConfig, FinalScoreWeights
from aurora.models import SignalItem
from aurora.pipeline import StageContext


def test_parse_json_object_accepts_plain_fenced_and_embedded_json() -> None:
    assert parse_json_object('{"score": 8}') == {"score": 8}
    assert parse_json_object('```json\n{"score": 8}\n```') == {"score": 8}
    assert parse_json_object('analysis: {"score": 8}') == {"score": 8}


def test_combine_scores_bounds_and_falls_back_to_deterministic() -> None:
    weights = FinalScoreWeights(deterministic=0.4, llm=0.6)

    assert combine_scores(8.0, None, weights) == 8.0
    assert combine_scores(None, 11.0, weights) == 10.0
    assert combine_scores(6.0, 9.0, weights) == 7.8


def test_llm_ranker_skips_when_missing_key_or_skip_flag() -> None:
    item = _item("news:1")
    ranker = LLMRanker(AIConfig(api_key_env="AURORA_TEST_MISSING_KEY"), weights=FinalScoreWeights())

    missing_key = asyncio.run(
        ranker.analyze_items([item], _prompt, StageContext(mode="test", run_id="test"))
    )
    skipped = asyncio.run(
        ranker.analyze_items(
            [item],
            _prompt,
            StageContext(mode="test", run_id="test", metadata={"skip_llm": True}),
        )
    )

    assert missing_key == {}
    assert skipped == {}


def test_llm_ranker_isolates_failures_and_applies_analysis() -> None:
    good = _item("news:good")
    bad = _item("news:bad")
    ranker = LLMRanker(
        AIConfig(api_key_env="AURORA_TEST_KEY"),
        weights=FinalScoreWeights(deterministic=0.5, llm=0.5),
        client=_FakeClient(),
    )

    analyses = asyncio.run(
        ranker.analyze_items([good, bad], _prompt, StageContext(mode="test", run_id="test"))
    )
    enriched = ranker.apply_analysis(good, analyses[good.id])
    fallback = ranker.apply_analysis(bad, analyses.get(bad.id))

    assert set(analyses) == {"news:good"}
    assert enriched.llm_score == 9.0
    assert enriched.final_score == 8.0
    assert enriched.summary == "LLM summary"
    assert enriched.why_it_matters == "LLM why"
    assert enriched.learning_value == "LLM learning"
    assert enriched.action_items == ["Read the source"]
    assert fallback.llm_score is None
    assert fallback.final_score == 7.0


def _prompt(item: SignalItem) -> tuple[str, str]:
    return "system", item.id


def _item(item_id: str) -> SignalItem:
    return SignalItem(
        id=item_id,
        type="news",
        title=item_id,
        url=f"https://example.com/{item_id.replace(':', '-')}",
        source="test",
        published_at=datetime(2026, 5, 25, tzinfo=timezone.utc),
        deterministic_score=7.0,
        final_score=7.0,
    )


class _FakeClient:
    def is_configured(self) -> bool:
        return True

    async def complete_json(self, system_prompt: str, user_prompt: str) -> dict:
        if "bad" in user_prompt:
            raise RuntimeError("boom")
        return LLMAnalysis(
            score=9.0,
            summary="LLM summary",
            why_it_matters="LLM why",
            learning_value="LLM learning",
            action_items=["Read the source"],
        ).model_dump()
