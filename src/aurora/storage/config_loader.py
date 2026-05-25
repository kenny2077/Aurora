"""Config loading helpers for Aurora."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from aurora.config import AuroraConfig


_ENV_PATTERN = re.compile(r"\$\{([^}]+)\}")


def expand_env_vars(value: Any) -> Any:
    """Recursively expand ${VAR} strings while preserving missing variables."""
    if isinstance(value, str):
        return _ENV_PATTERN.sub(
            lambda match: os.getenv(match.group(1), match.group(0)),
            value,
        )
    if isinstance(value, list):
        return [expand_env_vars(item) for item in value]
    if isinstance(value, dict):
        return {key: expand_env_vars(item) for key, item in value.items()}
    return value


def load_config(path: str | Path) -> AuroraConfig:
    """Read a JSON config file, expand ${VAR} values, and validate it."""
    config_path = Path(path)
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    expanded = expand_env_vars(raw)
    return AuroraConfig.model_validate(expanded)

