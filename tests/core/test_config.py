from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from aurora.config import AuroraConfig
from aurora.storage.config_loader import expand_env_vars, load_config


def test_aurora_config_defaults_match_pr1_contract() -> None:
    config = AuroraConfig()

    assert config.run.enabled_modes == ["tech_news", "scholar", "repo_learning"]
    assert config.run.timezone == "Asia/Shanghai"
    assert config.ai.provider == "deepseek"
    assert config.ai.model == "deepseek-chat"
    assert config.ai.api_key_env == "DEEPSEEK_API_KEY"
    assert config.pipeline.scoring.score_threshold == 7.0
    assert config.delivery.filesystem.enabled is True
    assert config.delivery.github_pages.enabled is True


@pytest.mark.parametrize(
    "payload",
    [
        {"run": {"time_window_hours": 0}},
        {"run": {"max_items": 0}},
        {"run": {"enabled_modes": []}},
        {"pipeline": {"scoring": {"score_threshold": 10.1}}},
        {"ai": {"analysis_concurrency": 0}},
        {"delivery": {"email": {"smtp_port": 70000}}},
    ],
)
def test_aurora_config_rejects_invalid_ranges(payload: dict) -> None:
    with pytest.raises(ValidationError):
        AuroraConfig.model_validate(payload)


def test_expand_env_vars_recurses_and_preserves_missing_or_literal_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AURORA_ROOT", "/tmp/aurora")

    expanded = expand_env_vars(
        {
            "path": "${AURORA_ROOT}/state.json",
            "missing": "${MISSING_AURORA_VAR}/state.json",
            "list": ["${AURORA_ROOT}", {"nested": "${AURORA_ROOT}/cache"}],
            "api_key_env": "DEEPSEEK_API_KEY",
        }
    )

    assert expanded["path"] == "/tmp/aurora/state.json"
    assert expanded["missing"] == "${MISSING_AURORA_VAR}/state.json"
    assert expanded["list"] == ["/tmp/aurora", {"nested": "/tmp/aurora/cache"}]
    assert expanded["api_key_env"] == "DEEPSEEK_API_KEY"


def test_load_config_reads_json_expands_env_and_validates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AURORA_STATE_DIR", str(tmp_path))
    config_path = tmp_path / "config.json"
    config_path.write_text(
        """
        {
          "run": {
            "state_path": "${AURORA_STATE_DIR}/state.json"
          },
          "ai": {
            "api_key_env": "DEEPSEEK_API_KEY"
          }
        }
        """,
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.run.state_path == tmp_path / "state.json"
    assert config.ai.api_key_env == "DEEPSEEK_API_KEY"

