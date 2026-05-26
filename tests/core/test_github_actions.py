from __future__ import annotations

from pathlib import Path


def test_github_actions_workflow_contains_schedule_manual_dispatch_and_pages_upload() -> None:
    workflow = Path(".github/workflows/aurora-digest.yml").read_text(encoding="utf-8")

    assert "schedule:" in workflow
    assert "workflow_dispatch:" in workflow
    assert "uv run aurora config validate" in workflow
    assert "uv run aurora run --mode" in workflow
    assert "SEMANTIC_SCHOLAR_API_KEY: ${{ secrets.SEMANTIC_SCHOLAR_API_KEY }}" in workflow
    assert "actions/upload-pages-artifact" in workflow
