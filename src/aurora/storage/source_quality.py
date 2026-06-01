"""Lightweight source quality history storage."""

from __future__ import annotations

import json
from pathlib import Path

from aurora.models import SourceStatus


SOURCE_QUALITY_PATH = Path("source_quality.json")


def update_source_quality(
    cache_dir: Path,
    *,
    mode: str,
    statuses: list[SourceStatus],
) -> dict[str, dict[str, float | int]]:
    """Update source quality history and return the full quality snapshot."""
    path = cache_dir / SOURCE_QUALITY_PATH
    data = _read_quality(path)
    for status in statuses:
        key = f"{mode}:{status.source}"
        entry = data.setdefault(
            key,
            {"runs": 0, "ok_runs": 0, "failed_runs": 0, "rate_limited_runs": 0},
        )
        entry["runs"] += 1
        if status.ok:
            entry["ok_runs"] += 1
        else:
            entry["failed_runs"] += 1
        if status.rate_limited:
            entry["rate_limited_runs"] += 1
        entry["quality_score"] = _quality_score(entry)
    _write_quality(path, data)
    return data


def _quality_score(entry: dict[str, int | float]) -> float:
    runs = max(1, int(entry.get("runs") or 0))
    ok_runs = int(entry.get("ok_runs") or 0)
    rate_limited_runs = int(entry.get("rate_limited_runs") or 0)
    return round(max(0.0, min(10.0, 10.0 * (ok_runs / runs) - rate_limited_runs)), 2)


def _read_quality(path: Path) -> dict[str, dict[str, int | float]]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    return {
        str(key): value
        for key, value in payload.items()
        if isinstance(value, dict)
    }


def _write_quality(path: Path, data: dict[str, dict[str, int | float]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, sort_keys=True, indent=2) + "\n", encoding="utf-8")
