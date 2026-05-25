"""Configured delivery stage for rendered Aurora digests."""

from __future__ import annotations

from aurora.config import AuroraConfig
from aurora.delivery.email import send_email
from aurora.delivery.filesystem import write_filesystem_report
from aurora.delivery.github_pages import write_pages_artifact
from aurora.delivery.webhook import send_webhooks
from aurora.models import DeliveryResult, RenderedDigest
from aurora.pipeline import StageContext


class ConfiguredDeliveryStage:
    """Deliver rendered digests through configured channels."""

    def __init__(self, config: AuroraConfig) -> None:
        self.config = config

    async def deliver(
        self, rendered: RenderedDigest, context: StageContext
    ) -> list[DeliveryResult]:
        if context.metadata.get("skip_delivery"):
            return [DeliveryResult(channel="delivery", metadata={"skipped": True})]

        results: list[DeliveryResult] = []
        if self.config.delivery.filesystem.enabled:
            results.append(_capture("filesystem", lambda: write_filesystem_report(rendered, context, self.config.delivery.filesystem)))
        if self.config.delivery.github_pages.enabled:
            results.append(_capture("github_pages", lambda: write_pages_artifact(rendered, context, self.config.delivery.github_pages)))
        if self.config.delivery.email.enabled:
            results.append(_capture("email", lambda: send_email(rendered, self.config.delivery.email)))
        if self.config.delivery.webhook.enabled:
            results.extend(await send_webhooks(rendered, context, self.config.delivery.webhook.targets))

        failed = [result for result in results if not result.ok]
        if failed and context.metadata.get("strict_delivery"):
            errors = "; ".join(f"{result.channel}: {result.error}" for result in failed)
            raise RuntimeError(f"delivery failed: {errors}")
        return results or [DeliveryResult(channel="delivery", metadata={"skipped": True})]


def _capture(channel: str, callback) -> DeliveryResult:
    try:
        return callback()
    except Exception as exc:
        return DeliveryResult(channel=channel, ok=False, error=str(exc))
