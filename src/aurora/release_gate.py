"""Persisted release readiness gate for scheduled Aurora digests."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from aurora.config import ReleaseGateConfig


def evaluate_release_gate(run_summary: dict[str, Any]) -> dict[str, Any]:
    """Return whether one run summary is clean enough for the release gate."""
    blockers: list[str] = []
    warnings: list[str] = []
    ai_usage = run_summary.get("ai_usage") if isinstance(run_summary.get("ai_usage"), dict) else {}
    public_copy = (
        run_summary.get("public_copy_quality")
        if isinstance(run_summary.get("public_copy_quality"), dict)
        else {}
    )

    if _int_value(ai_usage, "failed_calls") > 0:
        warnings.append("llm_failed_calls")
    if _int_value(ai_usage, "json_failures") > 0:
        warnings.append("llm_json_failures")
    if _int_value(ai_usage, "deterministic_fallbacks") > 0:
        warnings.append("deterministic_fallbacks")
    if _int_value(public_copy, "unresolved_selected") > 0:
        blockers.append("public_copy_unresolved")
    if _int_value(public_copy, "delivery_blocked") > 0:
        blockers.append("delivery_blocked")

    item_counts = run_summary.get("item_counts")
    minimums = run_summary.get("minimum_section_items")
    if isinstance(item_counts, dict) and isinstance(minimums, dict):
        for section, minimum in minimums.items():
            if _coerce_int(item_counts.get(section)) < _coerce_int(minimum):
                blockers.append(f"section_{section}_below_minimum")

    blockers = list(dict.fromkeys(blockers))
    warnings = list(dict.fromkeys(warnings))
    return {
        "run_id": str(run_summary.get("run_id") or ""),
        "mode": str(run_summary.get("mode") or ""),
        "clean": not blockers,
        "blockers": blockers,
        "warnings": warnings,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def record_release_gate_run(
    config: ReleaseGateConfig,
    run_summary: dict[str, Any],
    *,
    scheduled: bool,
) -> dict[str, Any]:
    """Record one scheduled run in the release-gate ledger and return status."""
    if not config.enabled or not scheduled:
        return load_release_gate_status(config)
    entry = evaluate_release_gate(run_summary)
    ledger = _load_ledger(config)
    runs = ledger.setdefault("runs", [])
    if not isinstance(runs, list):
        runs = []
        ledger["runs"] = runs
    runs.append(entry)
    ledger["runs"] = runs[-config.retain_runs :]
    ledger["updated_at"] = datetime.now(timezone.utc).isoformat()
    config.ledger_path.parent.mkdir(parents=True, exist_ok=True)
    config.ledger_path.write_text(
        json.dumps(ledger, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return _status_from_ledger(config, ledger)


def load_release_gate_status(config: ReleaseGateConfig) -> dict[str, Any]:
    """Load current release-gate status from disk."""
    return _status_from_ledger(config, _load_ledger(config))


def _load_ledger(config: ReleaseGateConfig) -> dict[str, Any]:
    try:
        payload = json.loads(config.ledger_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        payload = {}
    return payload if isinstance(payload, dict) else {}


def _status_from_ledger(config: ReleaseGateConfig, ledger: dict[str, Any]) -> dict[str, Any]:
    raw_runs = ledger.get("runs")
    runs = [run for run in raw_runs if isinstance(run, dict)] if isinstance(raw_runs, list) else []
    consecutive = 0
    for run in reversed(runs):
        if run.get("clean") is True:
            consecutive += 1
            continue
        break
    latest = runs[-1] if runs else None
    return {
        "enabled": config.enabled,
        "ledger_path": str(config.ledger_path),
        "required_clean_runs": config.required_clean_runs,
        "consecutive_clean_runs": consecutive,
        "ready": config.enabled and consecutive >= config.required_clean_runs,
        "total_recorded_runs": len(runs),
        "latest": latest,
    }


def _int_value(payload: object, key: str) -> int:
    if not isinstance(payload, dict):
        return 0
    return _coerce_int(payload.get(key))


def _coerce_int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0
