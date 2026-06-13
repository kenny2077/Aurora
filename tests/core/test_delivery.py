from __future__ import annotations

import asyncio
import smtplib
from datetime import datetime, timezone
from pathlib import Path

import pytest
import httpx

from aurora.config import (
    AuroraConfig,
    DeliveryConfig,
    EmailDeliveryConfig,
    FilesystemDeliveryConfig,
    GitHubPagesDeliveryConfig,
)
from aurora.delivery import ConfiguredDeliveryStage
from aurora.delivery.email import send_email
from aurora.delivery.webhook import send_webhooks
from aurora.models import RenderedDigest
from aurora.pipeline import StageContext


def test_configured_delivery_writes_filesystem_and_pages_artifacts(tmp_path: Path) -> None:
    config = AuroraConfig(
        delivery=DeliveryConfig(
            filesystem=FilesystemDeliveryConfig(reports_dir=tmp_path / "reports"),
            github_pages=GitHubPagesDeliveryConfig(publish_dir=tmp_path / "web" / "src" / "content" / "posts"),
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
    assert (tmp_path / "web" / "src" / "content" / "posts" / "2026-05-25-tech-news.md").exists()


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


def test_email_delivery_sends_html_alternative_when_present(monkeypatch) -> None:
    sent_messages = []

    class FakeSMTP:
        def __init__(self, host: str, port: int) -> None:
            self.host = host
            self.port = port

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def login(self, username: str, password: str) -> None:
            assert username == "sender@example.com"
            assert password == "password"

        def send_message(self, message) -> None:
            sent_messages.append(message)

    monkeypatch.setenv("AURORA_TEST_RECIPIENTS", "reader@example.com")
    monkeypatch.setenv("AURORA_TEST_SMTP_USERNAME", "sender@example.com")
    monkeypatch.setenv("AURORA_TEST_EMAIL_PASSWORD", "password")
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)

    result = send_email(
        RenderedDigest(
            mode="repo_learning",
            title="Aurora Repo Learning",
            markdown="# Plain text",
            html="<html><body><main>Product UI</main></body></html>",
        ),
        EmailDeliveryConfig(
            recipients_env="AURORA_TEST_RECIPIENTS",
            smtp_username_env="AURORA_TEST_SMTP_USERNAME",
            password_env="AURORA_TEST_EMAIL_PASSWORD",
        ),
    )

    assert result.ok is True
    assert len(sent_messages) == 1
    message = sent_messages[0]
    assert message.get_body(preferencelist=("plain",)).get_content().strip() == "# Plain text"
    assert "Product UI" in message.get_body(preferencelist=("html",)).get_content()


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


def test_webhook_delivery_rejects_non_https_urls_by_default() -> None:
    result = asyncio.run(
        send_webhooks(
            RenderedDigest(mode="tech_news", title="Tech", markdown="body"),
            StageContext(mode="tech_news", run_id="run-1"),
            [{"url": "http://example.com/webhook"}],
        )
    )[0]

    assert result.ok is False
    assert result.destination == "http://example.com/webhook"
    assert result.error == "webhook url must use https"


def test_webhook_delivery_redacts_secret_headers_from_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise RuntimeError(
            "Authorization: Bearer secret-token X-API-Key=secret-key password=hidden"
        )

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await send_webhooks(
                RenderedDigest(mode="tech_news", title="Tech", markdown="body"),
                StageContext(mode="tech_news", run_id="run-1"),
                [
                    {
                        "url": "https://example.com/webhook",
                        "headers": {
                            "Authorization": "Bearer secret-token",
                            "X-API-Key": "secret-key",
                        },
                    }
                ],
                http_client=client,
            )

    result = asyncio.run(exercise())[0]

    assert result.ok is False
    assert "secret-token" not in str(result.error)
    assert "secret-key" not in str(result.error)
    assert "hidden" not in str(result.error)
    assert "[REDACTED]" in str(result.error)
