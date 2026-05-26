from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from aurora.config import AuroraConfig, GitHubPagesDeliveryConfig
from aurora.delivery.github_pages import write_pages_artifact
from aurora.models import RenderedDigest
from aurora.pipeline import StageContext


def test_github_pages_delivery_writes_jekyll_site(tmp_path: Path) -> None:
    config = AuroraConfig()
    rendered = RenderedDigest(
        mode="unified_digest",
        title="Aurora Unified Digest",
        markdown="# Aurora Unified Digest\n\n## Research Papers\n\n1. [Paper](https://example.com) - 9.2/10",
        metadata={
            "item_counts": {"paper": 1, "repo": 0, "news": 0},
            "selected_item_ids": ["paper:one"],
        },
    )
    context = StageContext(
        mode="unified_digest",
        run_id="run-20260526T033547Z",
        until=datetime(2026, 5, 26, 3, 35, 47, tzinfo=timezone.utc),
        config=config,
    )

    result = write_pages_artifact(
        rendered,
        context,
        GitHubPagesDeliveryConfig(publish_dir=tmp_path / "site"),
    )

    site_dir = tmp_path / "site"
    post_path = site_dir / "_posts" / "2026-05-26-unified-digest.md"
    latest_path = site_dir / "unified_digest" / "index.md"

    assert result.destination == str(post_path)
    assert (site_dir / "_config.yml").read_text(encoding="utf-8").startswith('title: "Aurora"')
    assert 'baseurl: "/Aurora"' in (site_dir / "_config.yml").read_text(encoding="utf-8")
    assert (site_dir / "index.md").exists()
    assert (site_dir / "feed.xml").exists()
    assert (site_dir / "assets" / "css" / "aurora.css").exists()
    assert (site_dir / "repo_learning" / "index.md").exists()
    assert (site_dir / "scholar" / "index.md").exists()
    assert (site_dir / "tech_news" / "index.md").exists()
    assert latest_path.exists()
    index = (site_dir / "index.md").read_text(encoding="utf-8")
    css = (site_dir / "assets" / "css" / "aurora.css").read_text(encoding="utf-8")
    assert "## Latest Published Digest" in index
    assert "{{ latest.content }}" in index
    assert ".aurora-latest-digest" in css
    repo_page = (site_dir / "repo_learning" / "index.md").read_text(encoding="utf-8")
    assert "No dedicated Repo Learning digest has been published yet." in repo_page
    assert "latest published digest" in repo_page

    post = post_path.read_text(encoding="utf-8")
    assert "title: \"Aurora Unified Digest\"" in post
    assert "mode: \"unified_digest\"" in post
    assert "run_id: \"run-20260526T033547Z\"" in post
    assert "item_count: 1" in post
    assert "paper: 1" in post
    assert "# Aurora Unified Digest" in post
    assert "Aurora run did not publish a site artifact" not in post


def test_github_pages_latest_mode_page_links_archive_post(tmp_path: Path) -> None:
    context = StageContext(
        mode="tech_news",
        run_id="run-1",
        until=datetime(2026, 5, 25, 16, 0, tzinfo=timezone.utc),
        config=AuroraConfig(),
    )

    write_pages_artifact(
        RenderedDigest(
            mode="tech_news",
            title="Aurora Tech News",
            markdown="# Aurora Tech News\n\nSelected 2 tech news item(s).",
        ),
        context,
        GitHubPagesDeliveryConfig(publish_dir=tmp_path / "site"),
    )

    latest = (tmp_path / "site" / "tech_news" / "index.md").read_text(encoding="utf-8")
    assert "permalink: \"/tech_news/\"" in latest
    assert "/archive/2026-05-26-tech-news/" in latest
    assert "Selected 2 tech news item(s)." in latest
