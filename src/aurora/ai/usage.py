"""Shared optional-LLM usage accounting and budget helpers."""

from __future__ import annotations

import math
from typing import Any

from aurora.config import AIConfig, AITask
from aurora.pipeline import StageContext


def approx_tokens(value: str) -> int:
    """Return Aurora's deliberately conservative character-based token estimate."""
    text = str(value or "")
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 4))


def reserve_ai_budget(
    config: AIConfig,
    context: StageContext,
    prompt_tokens: int,
    task: AITask,
) -> bool:
    """Reserve one request and its prompt tokens, returning false on open fallback."""
    usage = ai_usage(config, context, task)
    usage["requested_calls"] += 1
    request_limit = config.max_requests_per_run
    token_limit = config.max_tokens_per_run
    reserved_before_this_call = usage["requested_calls"] - usage["skipped_by_budget"] - 1
    over_request_limit = request_limit is not None and reserved_before_this_call >= request_limit
    over_token_limit = token_limit is not None and usage["approx_total_tokens"] + prompt_tokens > token_limit
    if over_request_limit or over_token_limit:
        usage["skipped_by_budget"] += 1
        usage["deterministic_fallbacks"] += 1
        warn_budget_exhausted(context)
        if config.fail_open_on_budget_exceeded:
            return False
        raise RuntimeError("AI budget exhausted")
    usage["approx_prompt_tokens"] += prompt_tokens
    usage["approx_total_tokens"] += prompt_tokens
    _add_cost(usage, prompt_tokens, config.input_cost_per_million_tokens)
    return True


def reserve_ai_network_attempt(config: AIConfig, context: StageContext, task: AITask) -> bool:
    """Reserve one outbound attempt without double-counting prompt tokens."""
    usage = ai_usage(config, context, task)
    limit = config.max_network_attempts_per_run
    attempts = int(usage.get("network_attempts") or 0)
    if limit is not None and attempts >= limit:
        usage["skipped_by_budget"] += 1
        usage["deterministic_fallbacks"] += 1
        warning = "AI network-attempt budget exhausted; remaining items use deterministic scoring."
        warnings = context.metadata.setdefault("warnings", [])
        if isinstance(warnings, list) and warning not in warnings:
            warnings.append(warning)
        return False
    usage["network_attempts"] = attempts + 1
    return True


def record_ai_retry(config: AIConfig, context: StageContext, task: AITask) -> None:
    """Record a transient completion retry."""
    usage = ai_usage(config, context, task)
    usage["retried_calls"] += 1


def record_ai_success(
    config: AIConfig,
    context: StageContext,
    completion_tokens: int,
    task: AITask,
    latency_ms: int,
) -> None:
    """Record a completed optional LLM task."""
    usage = ai_usage(config, context, task)
    usage["succeeded_calls"] += 1
    usage["approx_completion_tokens"] += completion_tokens
    usage["approx_total_tokens"] += completion_tokens
    usage["latency_ms_total"] += latency_ms
    _add_cost(usage, completion_tokens, config.output_cost_per_million_tokens)


def record_ai_failure(
    config: AIConfig,
    context: StageContext,
    task: AITask,
    latency_ms: int,
    *,
    json_failure: bool = False,
    failure_category: str = "unknown",
) -> None:
    """Record a recoverable task failure and its deterministic fallback."""
    usage = ai_usage(config, context, task)
    usage["failed_calls"] += 1
    usage["latency_ms_total"] += latency_ms
    usage["deterministic_fallbacks"] += 1
    if json_failure:
        usage["json_failures"] += 1
    categories = usage.setdefault("failure_categories", {})
    if not isinstance(categories, dict):
        categories = {}
        usage["failure_categories"] = categories
    category = str(failure_category or "unknown")
    categories[category] = int(categories.get(category) or 0) + 1


def ai_usage(config: AIConfig, context: StageContext, task: AITask) -> dict[str, Any]:
    """Initialize and normalize redacted provider usage metadata."""
    default_cost = 0.0 if config.is_local_provider() else _configured_cost_default(config)
    usage = context.metadata.setdefault(
        "ai_usage",
        {
            "provider": config.provider,
            "model": config.model,
            "endpoint_kind": "local" if config.is_local_provider() else "cloud",
            "local_only": config.local_only,
            "task_models": {task: config.model},
            "requested_calls": 0,
            "network_attempts": 0,
            "retried_calls": 0,
            "succeeded_calls": 0,
            "failed_calls": 0,
            "skipped_by_budget": 0,
            "approx_prompt_tokens": 0,
            "approx_completion_tokens": 0,
            "approx_total_tokens": 0,
            "latency_ms_total": 0,
            "json_failures": 0,
            "deterministic_fallbacks": 0,
            "failure_categories": {},
            "estimated_cloud_cost_usd": default_cost,
        },
    )
    if not isinstance(usage, dict):
        usage = {}
        context.metadata["ai_usage"] = usage
    usage.setdefault("provider", config.provider)
    usage.setdefault("model", config.model)
    usage.setdefault("endpoint_kind", "local" if config.is_local_provider() else "cloud")
    usage.setdefault("local_only", config.local_only)
    task_models = usage.setdefault("task_models", {})
    if isinstance(task_models, dict):
        task_models[task] = config.model
    else:
        usage["task_models"] = {task: config.model}
    if config.is_local_provider():
        usage["estimated_cloud_cost_usd"] = 0.0
    else:
        usage.setdefault("estimated_cloud_cost_usd", _configured_cost_default(config))
    for key in (
        "requested_calls",
        "network_attempts",
        "retried_calls",
        "succeeded_calls",
        "failed_calls",
        "skipped_by_budget",
        "approx_prompt_tokens",
        "approx_completion_tokens",
        "approx_total_tokens",
        "latency_ms_total",
        "json_failures",
        "deterministic_fallbacks",
    ):
        try:
            usage[key] = int(usage.get(key) or 0)
        except (TypeError, ValueError):
            usage[key] = 0
    categories = usage.get("failure_categories")
    if not isinstance(categories, dict):
        usage["failure_categories"] = {}
    else:
        usage["failure_categories"] = {
            str(category): max(0, int(count or 0))
            for category, count in categories.items()
            if str(category).strip()
        }
    return usage


def warn_budget_exhausted(context: StageContext) -> None:
    warning = "AI budget exhausted; remaining items use deterministic scoring."
    warnings = context.metadata.setdefault("warnings", [])
    if isinstance(warnings, list) and warning not in warnings:
        warnings.append(warning)


def _configured_cost_default(config: AIConfig) -> float | None:
    if (
        config.input_cost_per_million_tokens is not None
        and config.output_cost_per_million_tokens is not None
    ):
        return 0.0
    return None


def _add_cost(usage: dict[str, Any], tokens: int, rate_per_million: float | None) -> None:
    current = usage.get("estimated_cloud_cost_usd")
    if not isinstance(current, (int, float)) or isinstance(current, bool) or rate_per_million is None:
        return
    usage["estimated_cloud_cost_usd"] = float(current) + (tokens * rate_per_million / 1_000_000)
