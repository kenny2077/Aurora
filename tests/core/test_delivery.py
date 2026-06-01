from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

from aurora.config import AuroraConfig, DeliveryConfig, FilesystemDeliveryConfig, GitHubPagesDeliveryConfig
from aurora.delivery import ConfiguredDeliveryStage
from aurora.models import RenderedDigest
from aurora.pipeline import StageContext


def test_configured_delivery_writes_filesystem_and_pages_artifacts(tmp_path: Path) -> None:
    config = AuroraConfig(
        delivery=DeliveryConfig(
            filesystem=FilesystemDeliveryConfig(reports_dir=tmp_path / "reports"),
            github_pages=GitHubPagesDeliveryConfig(publish_dir=tmp_path / "site"),
        )
    )
    rendered = RenderedDigest(mode="tech_news", title="Tech", markdown="# Tech\n\nBody")

    results = asyncio.run(
        ConfiguredDeliveryStage(config).deliver(
            rendered,
            StageContext(
                mode="tech_news",
                run_id="run-1",
                until=datetime(2026, 5, 25, tzinfo=timezone.utc),
                config=config,
            ),
        )
    )

    assert [result.channel for result in results] == ["filesystem", "github_pages"]
    assert (tmp_path / "reports" / "run-1" / "tech_news.md").read_text(encoding="utf-8") == "# Tech\n\nBody"
    assert (tmp_path / "reports" / "run-1" / "tech_news.html").exists()
    assert (tmp_path / "site" / "_config.yml").exists()
    assert (tmp_path / "site" / "index.md").exists()
    assert (tmp_path / "site" / "_posts" / "2026-05-25-tech-news.md").exists()
    assert (tmp_path / "site" / "tech_news" / "index.md").exists()


def test_filesystem_delivery_appends_source_health_when_run_summary_exists(tmp_path: Path) -> None:
    config = AuroraConfig(
        delivery=DeliveryConfig(
            filesystem=FilesystemDeliveryConfig(reports_dir=tmp_path / "reports"),
            github_pages=GitHubPagesDeliveryConfig(enabled=False),
        )
    )
    context = StageContext(
        mode="tech_news",
        run_id="run-1",
        metadata={
            "run_summary": {
                "counts": {"raw": 2, "normalized": 2, "deduplicated": 1, "enriched": 1},
                "source_health": {"ok": 1, "failed": 1, "rate_limited": 0},
                "sources": [
                    {"source": "hackernews", "ok": True, "fetched_count": 2},
                    {"source": "rss", "ok": False, "error": "timeout"},
                ],
            }
        },
        config=config,
    )

    results = asyncio.run(
        ConfiguredDeliveryStage(config).deliver(
            RenderedDigest(mode="tech_news", title="Tech", markdown="# Tech\n\nBody"),
            context,
        )
    )

    markdown = (tmp_path / "reports" / "run-1" / "tech_news.md").read_text(encoding="utf-8")
    assert results[0].ok is True
    assert "## Source Health" in markdown
    assert "Items: 2 raw -> 2 normalized -> 1 deduplicated -> 1 enriched." in markdown
    assert "Sources: 1 ok, 1 failed, 0 rate limited." in markdown
    assert "rss failed: timeout" in markdown


def test_configured_delivery_can_skip_delivery(tmp_path: Path) -> None:
    config = AuroraConfig(
        delivery=DeliveryConfig(
            filesystem=FilesystemDeliveryConfig(reports_dir=tmp_path / "reports"),
            github_pages=GitHubPagesDeliveryConfig(publish_dir=tmp_path / "site"),
        )
    )

    results = asyncio.run(
        ConfiguredDeliveryStage(config).deliver(
            RenderedDigest(mode="scholar", title="Scholar", markdown="body"),
            StageContext(mode="scholar", run_id="run-1", metadata={"skip_delivery": True}, config=config),
        )
    )

    assert results[0].metadata == {"skipped": True}
    assert not (tmp_path / "reports").exists()


def test_strict_delivery_raises_when_channel_fails(tmp_path: Path) -> None:
    config = AuroraConfig(
        delivery=DeliveryConfig(
            filesystem=FilesystemDeliveryConfig(enabled=False),
            github_pages=GitHubPagesDeliveryConfig(enabled=False),
            email={"enabled": True, "recipients_env": "AURORA_TEST_RECIPIENTS"},
        )
    )

    with pytest.raises(RuntimeError, match="delivery failed"):
        asyncio.run(
            ConfiguredDeliveryStage(config).deliver(
                RenderedDigest(mode="repo_learning", title="Repos", markdown="body"),
                StageContext(
                    mode="repo_learning",
                    run_id="run-1",
                    metadata={"strict_delivery": True},
                    config=config,
                ),
            )
        )
