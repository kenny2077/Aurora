from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

import pytest

from aurora.models import DeliveryResult, RenderedDigest, ScoreResult, SignalItem
from aurora.config import AuroraConfig, RunConfig
from aurora.pipeline import ModePipeline, PipelineRunner, StageContext


def _item(item_id: str = "news:1") -> SignalItem:
    return SignalItem(
        id=item_id,
        type="news",
        title=f"Signal {item_id}",
        url=f"https://example.com/{item_id.replace(':', '-')}",
        source="example",
        published_at="2026-05-25T00:00:00Z",
    )


class RecordingFetch:
    def __init__(self, name: str, records: Sequence[dict[str, Any]], calls: list[str]) -> None:
        self.name = name
        self.records = records
        self.calls = calls

    async def fetch(self, context: StageContext) -> Sequence[Any]:
        self.calls.append(f"fetch:{self.name}")
        return self.records


class FailingFetch:
    name = "bad_fetch"

    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    async def fetch(self, context: StageContext) -> Sequence[Any]:
        self.calls.append("fetch:bad_fetch")
        raise RuntimeError("fetch failed")


class RecordingNormalize:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    async def normalize(self, raw_items: Sequence[Any], context: StageContext) -> list[SignalItem]:
        self.calls.append("normalize")
        return [_item(f"news:{index}") for index, _ in enumerate(raw_items, start=1)]


class RecordingDeduplicate:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    async def deduplicate(
        self, items: Sequence[SignalItem], context: StageContext
    ) -> list[SignalItem]:
        self.calls.append("deduplicate")
        return list(items)


class RecordingScore:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    async def score(self, items: Sequence[SignalItem], context: StageContext) -> list[ScoreResult]:
        self.calls.append("score")
        return [ScoreResult(item_id=item.id, final_score=8.0) for item in items]


class RecordingEnrich:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    async def enrich(
        self,
        items: Sequence[SignalItem],
        score_results: Sequence[ScoreResult],
        context: StageContext,
    ) -> list[SignalItem]:
        self.calls.append("enrich")
        return list(items)


class RecordingSummarize:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    async def summarize(self, items: Sequence[SignalItem], context: StageContext) -> str:
        self.calls.append("summarize")
        return "summary"


class RecordingRender:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    async def render(
        self, summary: str, items: Sequence[SignalItem], context: StageContext
    ) -> RenderedDigest:
        self.calls.append("render")
        return RenderedDigest(mode=context.mode, title="Digest", markdown=summary)


class RecordingDeliver:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.rendered_metadata: dict[str, Any] | None = None

    async def deliver(
        self, rendered: RenderedDigest, context: StageContext
    ) -> list[DeliveryResult]:
        self.calls.append("deliver")
        self.rendered_metadata = rendered.metadata
        return [DeliveryResult(channel="dry_run")]


class FailingNormalize:
    async def normalize(self, raw_items: Sequence[Any], context: StageContext) -> list[SignalItem]:
        raise RuntimeError("normalize failed")


def _pipeline(calls: list[str], fetch_stages=None, normalize_stage=None) -> ModePipeline:
    return ModePipeline(
        mode="tech_news",
        fetch_stages=fetch_stages
        if fetch_stages is not None
        else [RecordingFetch("first", [{"id": "raw"}], calls)],
        normalize_stage=normalize_stage if normalize_stage is not None else RecordingNormalize(calls),
        deduplicate_stage=RecordingDeduplicate(calls),
        score_stage=RecordingScore(calls),
        enrich_stage=RecordingEnrich(calls),
        summarize_stage=RecordingSummarize(calls),
        render_stage=RecordingRender(calls),
        deliver_stage=RecordingDeliver(calls),
    )


def test_pipeline_runner_executes_fixed_stage_order(tmp_path) -> None:
    calls: list[str] = []
    context = StageContext(mode="tech_news", run_id="test")

    result = asyncio.run(PipelineRunner(tmp_path).run(_pipeline(calls), context))

    assert calls == [
        "fetch:first",
        "normalize",
        "deduplicate",
        "score",
        "enrich",
        "summarize",
        "render",
        "deliver",
    ]
    assert result.raw_count == 1
    assert result.normalized_count == 1
    assert result.deduplicated_count == 1
    assert result.score_result_count == 1
    assert result.enriched_count == 1
    assert result.delivery_results == [DeliveryResult(channel="dry_run")]
    assert set(result.output_paths) == {
        "normalized",
        "deduplicated",
        "score_results",
        "enriched",
        "run_summary",
    }


def test_pipeline_runner_aggregates_multiple_fetch_stages(tmp_path) -> None:
    calls: list[str] = []
    fetch_stages = [
        RecordingFetch("first", [{"id": "a"}], calls),
        RecordingFetch("second", [{"id": "b"}], calls),
    ]

    result = asyncio.run(
        PipelineRunner(tmp_path).run(
            _pipeline(calls, fetch_stages=fetch_stages),
            StageContext(mode="tech_news", run_id="multi"),
        )
    )

    assert result.raw_count == 2
    assert [status.fetched_count for status in result.source_statuses] == [1, 1]


def test_pipeline_runner_isolates_fetch_failures(tmp_path) -> None:
    calls: list[str] = []
    fetch_stages = [
        FailingFetch(calls),
        RecordingFetch("good_fetch", [{"id": "ok"}], calls),
    ]

    result = asyncio.run(
        PipelineRunner(tmp_path).run(
            _pipeline(calls, fetch_stages=fetch_stages),
            StageContext(mode="tech_news", run_id="partial"),
        )
    )

    assert result.raw_count == 1
    assert result.source_statuses[0].ok is False
    assert result.source_statuses[0].error == "fetch failed"
    assert result.source_statuses[1].ok is True
    assert "normalize" in calls


def test_pipeline_runner_attaches_run_summary_to_context_and_rendered_digest(tmp_path) -> None:
    calls: list[str] = []
    deliver = RecordingDeliver(calls)
    pipeline = _pipeline(
        calls,
        fetch_stages=[
            FailingFetch(calls),
            RecordingFetch("good_fetch", [{"id": "ok"}], calls),
        ],
    )
    pipeline = ModePipeline(
        mode=pipeline.mode,
        fetch_stages=pipeline.fetch_stages,
        normalize_stage=pipeline.normalize_stage,
        deduplicate_stage=pipeline.deduplicate_stage,
        score_stage=pipeline.score_stage,
        enrich_stage=pipeline.enrich_stage,
        summarize_stage=pipeline.summarize_stage,
        render_stage=pipeline.render_stage,
        deliver_stage=deliver,
    )
    context = StageContext(mode="tech_news", run_id="summary")

    result = asyncio.run(PipelineRunner(tmp_path).run(pipeline, context))

    run_summary = result.rendered_digest.metadata["run_summary"]
    assert context.metadata["run_summary"] == run_summary
    assert deliver.rendered_metadata == result.rendered_digest.metadata
    assert run_summary["mode"] == "tech_news"
    assert run_summary["run_id"] == "summary"
    assert run_summary["counts"] == {
        "raw": 1,
        "normalized": 1,
        "deduplicated": 1,
        "score_results": 1,
        "enriched": 1,
    }
    assert run_summary["source_health"] == {
        "total": 2,
        "ok": 1,
        "failed": 1,
        "rate_limited": 0,
    }
    assert run_summary["sources"][0]["source"] == "bad_fetch"
    assert run_summary["sources"][0]["ok"] is False
    assert run_summary["sources"][0]["error"] == "fetch failed"
    assert run_summary["sources"][1]["source"] == "good_fetch"
    assert run_summary["sources"][1]["fetched_count"] == 1


def test_pipeline_runner_writes_final_run_summary_json_with_delivery_results(tmp_path) -> None:
    calls: list[str] = []
    result = asyncio.run(
        PipelineRunner(tmp_path).run(
            _pipeline(
                calls,
                fetch_stages=[
                    RecordingFetch("good_fetch", [{"id": "ok"}], calls),
                ],
            ),
            StageContext(mode="tech_news", run_id="json-summary"),
        )
    )

    summary_path = tmp_path / "json-summary" / "tech_news" / "run_summary.json"
    assert result.output_paths["run_summary"] == summary_path
    payload = __import__("json").loads(summary_path.read_text(encoding="utf-8"))

    assert payload["run_id"] == "json-summary"
    assert payload["mode"] == "tech_news"
    assert payload["counts"]["enriched"] == 1
    assert payload["source_health"] == {"total": 1, "ok": 1, "failed": 0, "rate_limited": 0}
    assert payload["delivery_results"] == [
        {
            "channel": "dry_run",
            "destination": None,
            "error": None,
            "message_id": None,
            "metadata": {},
            "ok": True,
        }
    ]
    assert set(payload["output_paths"]) == {
        "normalized",
        "deduplicated",
        "score_results",
        "enriched",
        "run_summary",
    }


def test_pipeline_runner_redacts_secret_like_error_values_in_run_summary(tmp_path) -> None:
    calls: list[str] = []

    class SecretFailingFetch:
        name = "secret_fetch"

        async def fetch(self, context: StageContext) -> Sequence[Any]:
            raise RuntimeError("token=abc123 DEEPSEEK_API_KEY=secret Authorization: Bearer hidden")

    result = asyncio.run(
        PipelineRunner(tmp_path).run(
            _pipeline(
                calls,
                fetch_stages=[SecretFailingFetch(), RecordingFetch("good", [{"id": "ok"}], calls)],
            ),
            StageContext(mode="tech_news", run_id="redacted"),
        )
    )

    payload = __import__("json").loads(
        result.output_paths["run_summary"].read_text(encoding="utf-8")
    )
    error = payload["sources"][0]["error"]
    assert "abc123" not in error
    assert "secret" not in error
    assert "hidden" not in error
    assert "[REDACTED]" in error


def test_pipeline_runner_updates_source_quality_history(tmp_path) -> None:
    calls: list[str] = []
    config = AuroraConfig(run=RunConfig(cache_dir=tmp_path / "cache"))

    first = asyncio.run(
        PipelineRunner(tmp_path / "runs").run(
            _pipeline(
                calls,
                fetch_stages=[FailingFetch(calls), RecordingFetch("good", [{"id": "ok"}], calls)],
            ),
            StageContext(mode="tech_news", run_id="quality-1", config=config),
        )
    )
    second = asyncio.run(
        PipelineRunner(tmp_path / "runs").run(
            _pipeline(
                calls,
                fetch_stages=[FailingFetch(calls), RecordingFetch("good", [{"id": "ok"}], calls)],
            ),
            StageContext(mode="tech_news", run_id="quality-2", config=config),
        )
    )

    quality = second.rendered_digest.metadata["run_summary"]["source_quality"]
    assert quality["tech_news:bad_fetch"]["runs"] == 2
    assert quality["tech_news:bad_fetch"]["failed_runs"] == 2
    assert quality["tech_news:bad_fetch"]["quality_score"] == 0.0
    assert quality["tech_news:good"]["runs"] == 2
    assert quality["tech_news:good"]["ok_runs"] == 2
    assert quality["tech_news:good"]["quality_score"] == 10.0
    assert first.output_paths["run_summary"].exists()
    assert (tmp_path / "cache" / "source_quality.json").exists()


def test_pipeline_runner_propagates_non_fetch_failures(tmp_path) -> None:
    calls: list[str] = []

    with pytest.raises(RuntimeError, match="normalize failed"):
        asyncio.run(
            PipelineRunner(tmp_path).run(
                _pipeline(calls, normalize_stage=FailingNormalize()),
                StageContext(mode="tech_news", run_id="fail"),
            )
        )
