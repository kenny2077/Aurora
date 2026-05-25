"""Stable JSONL snapshot writing."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pydantic import BaseModel


def write_jsonl(path: str | Path, rows: Iterable[Any]) -> Path:
    """Write rows as stable JSONL and return the output path."""
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(_to_jsonable(row), sort_keys=True))
            handle.write("\n")
    return output_path


def _to_jsonable(row: Any) -> Any:
    if isinstance(row, BaseModel):
        return row.model_dump(mode="json")
    return row

