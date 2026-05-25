from __future__ import annotations

from pathlib import Path

from aurora.models import DeliveryResult, RenderedDigest, ScoreResult, SignalItem
from aurora.pipeline import ModePipeline, StageContext
from aurora.cli import main


def test_config_validate_succeeds_with_defaults(capsys) -> None:
    exit_code = main(["config", "validate"])

    assert exit_code == 0
    assert "config: ok" in capsys.readouterr().out


def test_config_validate_succeeds_with_temp_config(tmp_path: Path, capsys) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text('{"run": {"enabled_modes": ["tech_news"]}}', encoding="utf-8")

    exit_code = main(["config", "validate", "--config", str(config_path)])

    assert exit_code == 0
    assert "config: ok" in capsys.readouterr().out


def test_config_validate_invalid_config_exits_nonzero(tmp_path: Path, capsys) -> None:
    config_path = tmp_path / "bad.json"
    config_path.write_text('{"run": {"max_items": 0}}', encoding="utf-8")

    exit_code = main(["config", "validate", "--config", str(config_path)])

    assert exit_code == 1
    assert "error:" in capsys.readouterr().err


def test_run_dry_run_creates_expected_snapshot_files(tmp_path: Path, capsys) -> None:
    exit_code = main(
        [
            "run",
            "--dry-run",
            "--mode",
            "tech_news",
            "--output-dir",
            str(tmp_path),
        ]
    )

    assert exit_code == 0
    assert "tech_news: ok" in capsys.readouterr().out
    run_dir = tmp_path / "dry-run" / "tech_news"
    assert (run_dir / "normalized.jsonl").read_text(encoding="utf-8") == ""
    assert (run_dir / "deduplicated.jsonl").read_text(encoding="utf-8") == ""
    assert (run_dir / "score_results.jsonl").read_text(encoding="utf-8") == ""
    assert (run_dir / "enriched.jsonl").read_text(encoding="utf-8") == ""


def test_run_unimplemented_mode_without_dry_run_exits_nonzero(capsys) -> None:
    exit_code = main(["run", "--mode", "repo_learning"])

    assert exit_code == 2
    assert "mode not implemented yet: repo_learning" in capsys.readouterr().err


def test_run_mode_all_reports_unimplemented_default_modes(capsys) -> None:
    exit_code = main(["run", "--mode", "all"])

    assert exit_code == 2
    assert "mode not implemented yet: repo_learning" in capsys.readouterr().err


def test_run_mode_all_expands_to_config_enabled_modes(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    output_dir = tmp_path / "runs"
    config_path.write_text(
        """
        {
          "run": {
            "enabled_modes": ["tech_news", "scholar"]
          }
        }
        """,
        encoding="utf-8",
    )

    exit_code = main(
        [
            "run",
            "--dry-run",
            "--config",
            str(config_path),
            "--mode",
            "all",
            "--output-dir",
            str(output_dir),
        ]
    )

    assert exit_code == 0
    assert (output_dir / "dry-run" / "tech_news" / "normalized.jsonl").exists()
    assert (output_dir / "dry-run" / "scholar" / "normalized.jsonl").exists()


def test_real_tech_news_run_uses_pipeline_and_writes_non_empty_snapshots(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "config.json"
    output_dir = tmp_path / "runs"
    config_path.write_text('{"run": {"enabled_modes": ["tech_news"]}}', encoding="utf-8")
    monkeypatch.setattr("aurora.cli.build_tech_news_pipeline", _fake_pipeline)

    exit_code = main(
        [
            "run",
            "--mode",
            "tech_news",
            "--config",
            str(config_path),
            "--output-dir",
            str(output_dir),
            "--run-id",
            "test-run",
            "--hours",
            "12",
        ]
    )

    run_dir = output_dir / "test-run" / "tech_news"
    assert exit_code == 0
    assert (run_dir / "normalized.jsonl").read_text(encoding="utf-8") != ""
    assert (run_dir / "deduplicated.jsonl").read_text(encoding="utf-8") != ""
    assert (run_dir / "score_results.jsonl").read_text(encoding="utf-8") != ""
    assert (run_dir / "enriched.jsonl").read_text(encoding="utf-8") != ""


def test_real_scholar_run_uses_pipeline_and_writes_non_empty_snapshots(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "config.json"
    output_dir = tmp_path / "runs"
    config_path.write_text('{"run": {"enabled_modes": ["scholar"]}}', encoding="utf-8")
    monkeypatch.setattr("aurora.cli.build_scholar_pipeline", _fake_scholar_pipeline)

    exit_code = main(
        [
            "run",
            "--mode",
            "scholar",
            "--config",
            str(config_path),
            "--output-dir",
            str(output_dir),
            "--run-id",
            "test-run",
        ]
    )

    run_dir = output_dir / "test-run" / "scholar"
    assert exit_code == 0
    assert (run_dir / "normalized.jsonl").read_text(encoding="utf-8") != ""
    assert (run_dir / "deduplicated.jsonl").read_text(encoding="utf-8") != ""
    assert (run_dir / "score_results.jsonl").read_text(encoding="utf-8") != ""
    assert (run_dir / "enriched.jsonl").read_text(encoding="utf-8") != ""


class _Fetch:
    name = "fake"

    async def fetch(self, context: StageContext):
        return [{"id": "raw"}]


class _Normalize:
    async def normalize(self, raw_items, context: StageContext) -> list[SignalItem]:
        return [
            SignalItem(
                id="news:1",
                type="news",
                title="AI Signal",
                url="https://example.com/ai",
                source="fake",
                published_at=context.until,
            )
        ]


class _Deduplicate:
    async def deduplicate(self, items, context: StageContext) -> list[SignalItem]:
        return list(items)


class _Score:
    async def score(self, items, context: StageContext) -> list[ScoreResult]:
        return [ScoreResult(item_id=items[0].id, final_score=8.0)]


class _Enrich:
    async def enrich(self, items, score_results, context: StageContext) -> list[SignalItem]:
        return [items[0].model_copy(update={"final_score": 8.0})]


class _Summarize:
    async def summarize(self, items, context: StageContext) -> str:
        return "summary"


class _Render:
    async def render(self, summary, items, context: StageContext) -> RenderedDigest:
        return RenderedDigest(mode="tech_news", title="Tech News", markdown=summary)


class _Deliver:
    async def deliver(self, rendered, context: StageContext) -> list[DeliveryResult]:
        return [DeliveryResult(channel="dry_run")]


def _fake_pipeline(config) -> ModePipeline:
    return _mode_pipeline("tech_news", "news")


def _fake_scholar_pipeline(config) -> ModePipeline:
    return _mode_pipeline("scholar", "paper")


def _mode_pipeline(mode: str, item_type: str) -> ModePipeline:
    return ModePipeline(
        mode=mode,
        fetch_stages=[_Fetch()],
        normalize_stage=_Normalize(),
        deduplicate_stage=_Deduplicate(),
        score_stage=_Score(),
        enrich_stage=_Enrich(),
        summarize_stage=_Summarize(),
        render_stage=_Render(),
        deliver_stage=_Deliver(),
    )
