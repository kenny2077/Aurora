from __future__ import annotations

from pathlib import Path

from aurora.models import DeliveryResult, RenderedDigest, ScoreResult, SignalItem
from aurora.pipeline import ModePipeline, StageContext
from aurora.cli import main


def test_config_validate_succeeds_with_defaults(capsys) -> None:
    exit_code = main(["config", "validate"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "config: ok" in output
    assert "enabled modes:" in output
    assert "missing required env vars:" in output


def test_config_validate_succeeds_with_temp_config(tmp_path: Path, capsys) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text('{"run": {"enabled_modes": ["tech_news"]}}', encoding="utf-8")

    exit_code = main(["config", "validate", "--config", str(config_path)])

    assert exit_code == 0
    assert "config: ok" in capsys.readouterr().out


def test_doctor_reports_environment_without_crashing(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.delenv("SEMANTIC_SCHOLAR_API_KEY", raising=False)
    config_path = tmp_path / "config.json"
    config_path.write_text(
        """
        {
          "run": {"output_dir": "data/runs", "cache_dir": "data/cache"},
          "delivery": {
            "filesystem": {"reports_dir": "reports"},
            "github_pages": {"publish_dir": "site"}
          }
        }
        """,
        encoding="utf-8",
    )

    exit_code = main(["doctor", "--config", str(config_path)])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "doctor: ok" in output
    assert "missing optional env vars:" in output
    assert "SEMANTIC_SCHOLAR_API_KEY" in output
    assert "pages branch: gh-pages" in output
    assert "run summaries: enabled" in output
    assert "email delivery: disabled" in output


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


def test_dry_run_smoke_all_modes_including_unified_digest(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    output_dir = tmp_path / "runs"
    config_path.write_text(
        """
        {
          "run": {
            "enabled_modes": ["tech_news", "scholar", "repo_learning", "unified_digest"]
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
    assert (output_dir / "dry-run" / "repo_learning" / "normalized.jsonl").exists()
    assert (output_dir / "dry-run" / "unified_digest" / "normalized.jsonl").exists()


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


def test_real_scholar_run_applies_research_field_override(
    tmp_path: Path, monkeypatch
) -> None:
    observed_fields: list[str] = []
    config_path = tmp_path / "config.json"
    output_dir = tmp_path / "runs"
    config_path.write_text('{"run": {"enabled_modes": ["scholar"]}}', encoding="utf-8")

    def fake_pipeline(config) -> ModePipeline:
        observed_fields.extend(config.modes.scholar.fields)
        return _fake_scholar_pipeline(config)

    monkeypatch.setattr("aurora.cli.build_scholar_pipeline", fake_pipeline)

    exit_code = main(
        [
            "run",
            "--mode",
            "scholar",
            "--config",
            str(config_path),
            "--output-dir",
            str(output_dir),
            "--research-field",
            "ml",
            "--research-field",
            "agents",
        ]
    )

    assert exit_code == 0
    assert observed_fields == ["ml", "agents"]


def test_real_repo_learning_run_uses_pipeline_and_writes_non_empty_snapshots(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "config.json"
    output_dir = tmp_path / "runs"
    config_path.write_text('{"run": {"enabled_modes": ["repo_learning"]}}', encoding="utf-8")
    monkeypatch.setattr("aurora.cli.build_repo_learning_pipeline", _fake_repo_learning_pipeline)

    exit_code = main(
        [
            "run",
            "--mode",
            "repo_learning",
            "--config",
            str(config_path),
            "--output-dir",
            str(output_dir),
            "--run-id",
            "test-run",
        ]
    )

    run_dir = output_dir / "test-run" / "repo_learning"
    assert exit_code == 0
    assert (run_dir / "normalized.jsonl").read_text(encoding="utf-8") != ""
    assert (run_dir / "deduplicated.jsonl").read_text(encoding="utf-8") != ""
    assert (run_dir / "score_results.jsonl").read_text(encoding="utf-8") != ""
    assert (run_dir / "enriched.jsonl").read_text(encoding="utf-8") != ""


def test_real_repo_learning_run_applies_repo_interest_override(
    tmp_path: Path, monkeypatch
) -> None:
    observed_interests: list[str] = []
    config_path = tmp_path / "config.json"
    output_dir = tmp_path / "runs"
    config_path.write_text('{"run": {"enabled_modes": ["repo_learning"]}}', encoding="utf-8")

    def fake_pipeline(config) -> ModePipeline:
        observed_interests.extend(config.modes.repo_learning.interests)
        return _fake_repo_learning_pipeline(config)

    monkeypatch.setattr("aurora.cli.build_repo_learning_pipeline", fake_pipeline)

    exit_code = main(
        [
            "run",
            "--mode",
            "repo_learning",
            "--config",
            str(config_path),
            "--output-dir",
            str(output_dir),
            "--repo-interest",
            "agents",
            "--repo-interest",
            "cv",
        ]
    )

    assert exit_code == 0
    assert observed_interests == ["agents", "cv"]


def test_real_unified_digest_run_uses_pipeline_and_writes_snapshots(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "config.json"
    output_dir = tmp_path / "runs"
    config_path.write_text('{"run": {"enabled_modes": ["unified_digest"]}}', encoding="utf-8")
    monkeypatch.setattr("aurora.cli.build_unified_digest_pipeline", _fake_unified_pipeline)

    exit_code = main(
        [
            "run",
            "--mode",
            "unified_digest",
            "--config",
            str(config_path),
            "--output-dir",
            str(output_dir),
            "--run-id",
            "test-run",
        ]
    )

    run_dir = output_dir / "test-run" / "unified_digest"
    assert exit_code == 0
    assert (run_dir / "normalized.jsonl").read_text(encoding="utf-8") != ""
    assert (run_dir / "enriched.jsonl").read_text(encoding="utf-8") != ""


def test_run_prints_source_health_summary(tmp_path: Path, monkeypatch, capsys) -> None:
    config_path = tmp_path / "config.json"
    output_dir = tmp_path / "runs"
    config_path.write_text('{"run": {"enabled_modes": ["tech_news"]}}', encoding="utf-8")
    monkeypatch.setattr("aurora.cli.build_tech_news_pipeline", _partially_failing_pipeline)

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
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "tech_news: sources 1 ok, 1 failed, 0 rate limited" in output
    assert "tech_news: source bad_fetch failed - fetch failed" in output


def test_run_prints_run_summary_warnings(tmp_path: Path, monkeypatch, capsys) -> None:
    config_path = tmp_path / "config.json"
    output_dir = tmp_path / "runs"
    config_path.write_text('{"run": {"enabled_modes": ["unified_digest"]}}', encoding="utf-8")
    monkeypatch.setattr("aurora.cli.build_unified_digest_pipeline", _warning_pipeline)

    exit_code = main(
        [
            "run",
            "--mode",
            "unified_digest",
            "--config",
            str(config_path),
            "--output-dir",
            str(output_dir),
            "--run-id",
            "test-run",
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert (
        "unified_digest: warning scholar: Semantic Scholar enrichment rate-limited; "
        "deterministic scholar scoring used."
    ) in output


class _Fetch:
    name = "fake"

    async def fetch(self, context: StageContext):
        return [{"id": "raw"}]


class _FailingFetch:
    name = "bad_fetch"

    async def fetch(self, context: StageContext):
        raise RuntimeError("fetch failed")


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


class _WarningEnrich:
    async def enrich(self, items, score_results, context: StageContext) -> list[SignalItem]:
        context.metadata["unified_child_run_summaries"] = [
            {
                "mode": "scholar",
                "warnings": [
                    "Semantic Scholar enrichment rate-limited; deterministic scholar scoring used."
                ],
            }
        ]
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


def _fake_repo_learning_pipeline(config) -> ModePipeline:
    return _mode_pipeline("repo_learning", "repo")


def _fake_unified_pipeline(config) -> ModePipeline:
    return _mode_pipeline("unified_digest", "news")


def _partially_failing_pipeline(config) -> ModePipeline:
    pipeline = _mode_pipeline("tech_news", "news")
    return ModePipeline(
        mode=pipeline.mode,
        fetch_stages=[_FailingFetch(), _Fetch()],
        normalize_stage=pipeline.normalize_stage,
        deduplicate_stage=pipeline.deduplicate_stage,
        score_stage=pipeline.score_stage,
        enrich_stage=pipeline.enrich_stage,
        summarize_stage=pipeline.summarize_stage,
        render_stage=pipeline.render_stage,
        deliver_stage=pipeline.deliver_stage,
    )


def _warning_pipeline(config) -> ModePipeline:
    pipeline = _mode_pipeline("unified_digest", "news")
    return ModePipeline(
        mode=pipeline.mode,
        fetch_stages=pipeline.fetch_stages,
        normalize_stage=pipeline.normalize_stage,
        deduplicate_stage=pipeline.deduplicate_stage,
        score_stage=pipeline.score_stage,
        enrich_stage=_WarningEnrich(),
        summarize_stage=pipeline.summarize_stage,
        render_stage=pipeline.render_stage,
        deliver_stage=pipeline.deliver_stage,
    )


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
