from __future__ import annotations

from pathlib import Path

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


def test_run_without_dry_run_exits_nonzero(capsys) -> None:
    exit_code = main(["run", "--mode", "tech_news"])

    assert exit_code == 2
    assert "only dry-run is available in PR 2" in capsys.readouterr().err


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

