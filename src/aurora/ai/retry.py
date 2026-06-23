"""Bounded retry helpers for optional JSON completions."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from aurora.ai.providers import AIResponseFormatError
from aurora.ai.usage import record_ai_retry, reserve_ai_network_attempt
from aurora.config import AIConfig, AITask
from aurora.pipeline import StageContext


async def complete_json_with_retries(
    client: Any,
    config: AIConfig,
    context: StageContext,
    task: AITask,
    system_prompt: str,
    user_prompt: str,
) -> dict[str, Any] | None:
    """Return a completion or ``None`` when the outbound-attempt budget is exhausted."""
    for attempt in range(config.transient_retry_attempts + 1):
        if not reserve_ai_network_attempt(config, context, task):
            return None
        try:
            return await client.complete_json(system_prompt, user_prompt)
        except Exception as exc:
            if not _is_retryable(exc) or attempt >= config.transient_retry_attempts:
                raise
            record_ai_retry(config, context, task)
            if config.retry_backoff_sec:
                await asyncio.sleep(config.retry_backoff_sec * (2**attempt))
    return None


def classify_ai_failure(exc: Exception) -> str:
    """Return a stable, secret-free category for an optional completion failure."""
    if isinstance(exc, AIResponseFormatError):
        return "invalid_response"
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.RequestError):
        return "connection"
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in {401, 403}:
            return "authentication"
        if status == 429:
            return "rate_limit"
        if status == 408:
            return "timeout"
        if status >= 500:
            return "server_error"
        return "provider_error"
    return "unknown"


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, (AIResponseFormatError, httpx.TimeoutException, httpx.RequestError)):
        return not isinstance(exc, AIResponseFormatError)
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code == 408 or exc.response.status_code >= 500
    return False
