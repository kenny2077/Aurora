from __future__ import annotations

from pathlib import Path


def test_readme_quickstart_commands_cover_core_modes() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "daily learning radar for AI builders, researchers, and students" in readme
    assert "Today's Learning Workflow" in readme
    assert "one paper to understand" in readme
    assert "one repo to study" in readme
    assert "10-Minute Setup" in readme
    assert "What Aurora Is Not" in readme
    assert "GitHub Pages Setup" in readme
    assert "Troubleshooting Empty Sections" in readme
    assert "gh-pages" in readme
    assert "run_summary.json" in readme
    assert "SMTP_USERNAME, EMAIL_PASSWORD, AURORA_EMAIL_RECIPIENTS" in readme
    assert "DEEPSEEK_API_KEY" in readme
    assert "GH_SEARCH_TOKEN" in readme
    assert "GITHUB_TOKEN fallback" in readme
    assert "SEMANTIC_SCHOLAR_API_KEY" in readme
    assert "aurora run --mode repo_learning --repo-interest agents" in readme
    assert "aurora run --mode repo_learning --repo-interest cv" in readme
    assert "aurora run --mode repo_learning --repo-interest mcp" in readme
    assert "aurora run --mode scholar --research-field ml" in readme
    assert "aurora run --mode scholar --research-field ml --research-field agents" in readme
    assert "aurora run --mode unified_digest --config data/config.example.json" in readme


def test_interest_docs_document_migration_and_presets() -> None:
    docs = Path("docs/interests.md").read_text(encoding="utf-8")

    assert "daily learning radar" in docs
    assert "learning interests" in docs
    assert "research fields" in docs
    assert "`agents`" in docs
    assert "`cv`" in docs
    assert "`ml`" in docs
    assert "Legacy `modes.repo_learning.sources.github_search.domains` remains supported" in docs
