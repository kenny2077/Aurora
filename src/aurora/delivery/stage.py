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
        rendered = _with_source_health(rendered, context)

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


def _with_source_health(rendered: RenderedDigest, context: StageContext) -> RenderedDigest:
    if "## Source Health" in rendered.markdown or "## Run Summary" in rendered.markdown:
        return rendered
    run_summary = context.metadata.get("run_summary")
    if not isinstance(run_summary, dict):
        return rendered
    lines = _source_health_lines(run_summary)
    if not lines:
        return rendered
    return rendered.model_copy(
        update={"markdown": f"{rendered.markdown.rstrip()}\n\n{chr(10).join(lines)}"}
    )


def _source_health_lines(run_summary: dict) -> list[str]:
    counts = run_summary.get("counts")
    health = run_summary.get("source_health")
    if not isinstance(counts, dict) and not isinstance(health, dict):
        return []
    lines = ["## Source Health", ""]
    if isinstance(counts, dict):
        lines.append(
            "Items: "
            f"{int(counts.get('raw') or 0)} raw -> "
            f"{int(counts.get('normalized') or 0)} normalized -> "
            f"{int(counts.get('deduplicated') or 0)} deduplicated -> "
            f"{int(counts.get('enriched') or 0)} enriched."
        )
    if isinstance(health, dict):
        lines.append(
            "Sources: "
            f"{int(health.get('ok') or 0)} ok, "
            f"{int(health.get('failed') or 0)} failed, "
            f"{int(health.get('rate_limited') or 0)} rate limited."
        )
    sources = run_summary.get("sources")
    if isinstance(sources, list):
        for source in sources:
            if not isinstance(source, dict) or source.get("ok", True):
                continue
            lines.append(
                f"{source.get('source') or 'unknown'} failed: "
                f"{source.get('error') or 'unknown error'}"
            )
    return lines
