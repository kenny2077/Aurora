from __future__ import annotations

from pathlib import Path


def test_github_actions_workflow_publishes_site_to_gh_pages_branch() -> None:
    workflow = Path(".github/workflows/aurora-digest.yml").read_text(encoding="utf-8")

    assert "schedule:" in workflow
    assert "workflow_dispatch:" in workflow
    assert "topic:" in workflow
    assert "default: agents_harness" in workflow
    assert "machine_learning" in workflow
    assert "agents_harness" in workflow
    assert "computer_vision" in workflow
    assert "skip_llm:" in workflow
    assert "default: \"false\"" in workflow
    assert "FORCE_JAVASCRIPT_ACTIONS_TO_NODE24: \"true\"" in workflow
    assert "aurora:" in workflow
    assert "timeout-minutes: 30" in workflow
    assert "timeout-minutes: 25" in workflow
    assert "working-directory: target" not in workflow
    assert "contents: write" in workflow
    assert "persist-credentials: false" in workflow
    assert "actions/checkout@v5" in workflow
    assert "actions/setup-node@v6" in workflow
    assert "node-version: \"24\"" in workflow
    assert "cache-dependency-path: web/package-lock.json" in workflow
    assert "python -m pip install --user \"uv==0.11.15\"" in workflow
    assert "npm --prefix web ci" in workflow
    assert "uv run aurora config validate --config data/actions.config.json" in workflow
    assert "Missing required email secret" in workflow
    assert "Missing required LLM secret: DEEPSEEK_API_KEY" in workflow
    assert "Restore Aurora state" in workflow
    assert ".aurora/aurora_state.json" in workflow
    assert ".aurora/content/posts" in workflow
    assert "cp -R \"$STATE_DIR/.aurora/content/posts/.\" web/src/content/posts/" in workflow
    assert "CONFIG_PATH=\"data/actions.config.json\"" in workflow
    assert "TOPIC=\"${{ github.event.inputs.topic || 'agents_harness' }}\"" in workflow
    assert "GITHUB_TOKEN: ${{ github.token }}" in workflow
    assert "ARGS=(--config \"$CONFIG_PATH\" --mode \"$MODE\")" in workflow
    assert "ARGS+=(--topic \"$TOPIC\")" in workflow
    assert "SKIP_LLM=\"${{ github.event.inputs.skip_llm || 'false' }}\"" in workflow
    assert "ARGS+=(--skip-llm)" in workflow
    assert "uv run aurora run \"${ARGS[@]}\" --strict-delivery" in workflow
    assert "SEMANTIC_SCHOLAR_API_KEY: ${{ secrets.SEMANTIC_SCHOLAR_API_KEY }}" in workflow
    assert "npm --prefix web run build" in workflow
    assert "test -s web/dist/index.html" in workflow
    assert "test -s web/dist/rss.xml" in workflow
    assert "find web/src/content/posts -type f -name '*.md'" in workflow
    assert "No items were available for the unified digest." in workflow
    assert "refusing to publish an empty Pages update" in workflow
    assert "cp -R web/src/content/posts/. web/dist/.aurora/content/posts/" in workflow
    assert "cp data/aurora_state.json web/dist/.aurora/aurora_state.json" in workflow
    assert "touch web/dist/.nojekyll" in workflow
    assert "git -C \"$PUBLISH_DIR\" fetch --depth=1 origin gh-pages" in workflow
    assert "cp -R web/dist/. \"$PUBLISH_DIR\"/" in workflow
    assert "rm \"$PUBLISH_DIR/.nojekyll\"" not in workflow
    assert "git -C \"$PUBLISH_DIR\" push origin gh-pages" in workflow
    assert "astral-sh/setup-uv" not in workflow
    assert "actions/upload-artifact" not in workflow
    assert "actions/download-artifact" not in workflow
    assert "peaceiris/actions-gh-pages" not in workflow
    assert "actions/upload-pages-artifact" not in workflow
    assert "Aurora run did not publish a site artifact" not in workflow
    assert "target/site" not in workflow
