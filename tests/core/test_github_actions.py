from __future__ import annotations

from pathlib import Path


def test_github_actions_workflow_publishes_site_to_gh_pages_branch() -> None:
    workflow = Path(".github/workflows/aurora-digest.yml").read_text(encoding="utf-8")

    assert "schedule:" in workflow
    assert "workflow_dispatch:" in workflow
    assert "skip_llm:" in workflow
    assert "default: \"true\"" in workflow
    assert "build-site:" in workflow
    assert "publish-pages:" in workflow
    assert "needs: build-site" in workflow
    assert "timeout-minutes: 30" in workflow
    assert "timeout-minutes: 25" in workflow
    assert "working-directory: target" not in workflow
    assert "contents: read" in workflow
    assert "contents: write" in workflow
    assert "persist-credentials: false" in workflow
    assert "actions/checkout@v5" in workflow
    assert "astral-sh/setup-uv@v6" in workflow
    assert "uv run aurora config validate" in workflow
    assert "ARGS=(--mode \"$MODE\")" in workflow
    assert "ARGS+=(--skip-llm)" in workflow
    assert "uv run aurora run \"${ARGS[@]}\" --strict-delivery" in workflow
    assert "SEMANTIC_SCHOLAR_API_KEY: ${{ secrets.SEMANTIC_SCHOLAR_API_KEY }}" in workflow
    assert "test -s site/index.md" in workflow
    assert "actions/upload-artifact@v5" in workflow
    assert "actions/download-artifact@v5" in workflow
    assert "name: aurora-site" in workflow
    assert "peaceiris/actions-gh-pages@v4" in workflow
    assert "publish_branch: gh-pages" in workflow
    assert "publish_dir: ./site" in workflow
    assert "keep_files: true" in workflow
    assert "actions/upload-pages-artifact" not in workflow
    assert "Aurora run did not publish a site artifact" not in workflow
    assert "target/site" not in workflow
