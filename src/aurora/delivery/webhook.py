"""Webhook delivery."""

from __future__ import annotations

from typing import Any

import httpx

from aurora.models import DeliveryResult, RenderedDigest
from aurora.pipeline import StageContext


async def send_webhooks(
    rendered: RenderedDigest,
    context: StageContext,
    targets: list[dict[str, Any]],
) -> list[DeliveryResult]:
    """Send rendered Markdown to configured webhook targets."""
    if not targets:
        return []
    results: list[DeliveryResult] = []
    async with httpx.AsyncClient(timeout=20.0) as client:
        for index, target in enumerate(targets):
            url = str(target.get("url") or "").strip()
            if not url:
                results.append(
                    DeliveryResult(channel="webhook", ok=False, error="missing webhook url")
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
                    DeliveryResult(channel="webhook", ok=False, destination=url, error=str(exc))
                )
    return results
