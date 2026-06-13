"""Webhook delivery."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

import httpx

from aurora.models import DeliveryResult, RenderedDigest
from aurora.pipeline import StageContext


async def send_webhooks(
    rendered: RenderedDigest,
    context: StageContext,
    targets: list[dict[str, Any]],
    *,
    http_client: httpx.AsyncClient | None = None,
) -> list[DeliveryResult]:
    """Send rendered Markdown to configured webhook targets."""
    if not targets:
        return []
    results: list[DeliveryResult] = []
    if http_client is not None:
        return await _send_with_client(rendered, context, targets, http_client)
    async with httpx.AsyncClient(timeout=20.0) as client:
        return await _send_with_client(rendered, context, targets, client)


async def _send_with_client(
    rendered: RenderedDigest,
    context: StageContext,
    targets: list[dict[str, Any]],
    client: httpx.AsyncClient,
) -> list[DeliveryResult]:
    results: list[DeliveryResult] = []
    for index, target in enumerate(targets):
        url = str(target.get("url") or "").strip()
        if not url:
            results.append(DeliveryResult(channel="webhook", ok=False, error="missing webhook url"))
            continue
        if not _webhook_url_allowed(url, target):
            results.append(
                DeliveryResult(
                    channel="webhook",
                    ok=False,
                    destination=url,
                    error="webhook url must use https",
                    metadata={"target_index": index},
                )
            )
            continue
        try:
            response = await client.post(
                url,
                json={
                    "run_id": context.run_id,
                    "mode": rendered.mode,
                    "title": rendered.title,
                    "markdown": rendered.markdown,
                    "metadata": rendered.metadata,
                },
                headers=dict(target.get("headers") or {}),
            )
            response.raise_for_status()
            results.append(
                DeliveryResult(
                    channel="webhook",
                    destination=url,
                    message_id=str(response.status_code),
                    metadata={"target_index": index},
                )
            )
        except Exception as exc:
            results.append(
                DeliveryResult(
                    channel="webhook",
                    ok=False,
                    destination=url,
                    error=_redact_secret_like_text(str(exc)),
                    metadata={"target_index": index},
                )
            )
    return results


def _webhook_url_allowed(url: str, target: dict[str, Any]) -> bool:
    parsed = urlparse(url)
    if parsed.scheme == "https" and parsed.netloc:
        return True
    return bool(target.get("allow_insecure"))


def _redact_secret_like_text(value: str) -> str:
    patterns = [
        r"(?i)(authorization:\s*bearer\s+)[^\s]+",
        r"(?i)([a-z0-9_-]*(?:token|key|secret|password)[a-z0-9_-]*=)[^\s]+",
    ]
    redacted = value
    for pattern in patterns:
        redacted = re.sub(pattern, r"\1[REDACTED]", redacted)
    return redacted
