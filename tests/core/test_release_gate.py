from __future__ import annotations

from pathlib import Path

from aurora.config import ReleaseGateConfig
from aurora.release_gate import evaluate_release_gate, load_release_gate_status, record_release_gate_run


def test_release_gate_requires_consecutive_clean_scheduled_runs(tmp_path: Path) -> None:
    ledger_path = tmp_path / "release_gate.json"
    config = ReleaseGateConfig(enabled=True, ledger_path=ledger_path, required_clean_runs=3)

    record_release_gate_run(config, _summary("run-1"), scheduled=True)
    record_release_gate_run(config, _summary("run-2", unresolved=1), scheduled=True)
    record_release_gate_run(config, _summary("run-3"), scheduled=True)
    record_release_gate_run(config, _summary("run-4"), scheduled=True)

    status = load_release_gate_status(config)

    assert status["ready"] is False
    assert status["consecutive_clean_runs"] == 2
    assert status["required_clean_runs"] == 3
    assert status["latest"]["clean"] is True


def test_release_gate_ignores_manual_runs_and_reports_blockers(tmp_path: Path) -> None:
    ledger_path = tmp_path / "release_gate.json"
    config = ReleaseGateConfig(enabled=True, ledger_path=ledger_path, required_clean_runs=1)
    blocked_summary = _summary(
        "manual",
        ai_failed=2,
        fallbacks=2,
        unresolved=1,
        delivery_blocked=1,
        missing_papers=True,
    )

    record_release_gate_run(config, blocked_summary, scheduled=False)
    status = load_release_gate_status(config)
    evaluated = evaluate_release_gate(blocked_summary)

    assert status["total_recorded_runs"] == 0
    assert status["ready"] is False
    assert set(evaluated["blockers"]) >= {
        "public_copy_unresolved",
        "delivery_blocked",
        "section_paper_below_minimum",
    }
    assert set(evaluated["warnings"]) >= {
        "llm_failed_calls",
        "llm_json_failures",
        "deterministic_fallbacks",
    }


def test_release_gate_keeps_optional_llm_failures_as_warnings_when_public_output_is_clean() -> None:
    evaluated = evaluate_release_gate(_summary("cheap-clean", ai_failed=2, fallbacks=3))

    assert evaluated["clean"] is True
    assert evaluated["blockers"] == []
    assert set(evaluated["warnings"]) == {
        "llm_failed_calls",
        "llm_json_failures",
        "deterministic_fallbacks",
    }


def _summary(
    run_id: str,
    *,
    ai_failed: int = 0,
    fallbacks: int = 0,
    unresolved: int = 0,
    delivery_blocked: int = 0,
    missing_papers: bool = False,
) -> dict:
    return {
        "run_id": run_id,
        "mode": "unified_digest",
        "ai_usage": {
            "failed_calls": ai_failed,
            "json_failures": ai_failed,
            "deterministic_fallbacks": fallbacks,
        },
        "public_copy_quality": {
            "unresolved_selected": unresolved,
            "delivery_blocked": delivery_blocked,
        },
        "item_counts": {"news": 5, "repo": 3, "paper": 2 if missing_papers else 3},
        "minimum_section_items": {"news": 5, "repo": 3, "paper": 3},
    }
