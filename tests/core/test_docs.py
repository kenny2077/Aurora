from __future__ import annotations

from pathlib import Path


def test_readme_quickstart_commands_cover_core_modes() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "aurora run --mode repo_learning --repo-interest agents" in readme
    assert "aurora run --mode repo_learning --repo-interest cv" in readme
    assert "aurora run --mode scholar --research-field ml" in readme
    assert "aurora run --mode unified_digest --config data/config.example.json" in readme


def test_interest_docs_document_migration_and_presets() -> None:
    docs = Path("docs/interests.md").read_text(encoding="utf-8")

    assert "`agents`" in docs
    assert "`cv`" in docs
    assert "`ml`" in docs
    assert "Legacy `modes.repo_learning.sources.github_search.domains` remains supported" in docs
