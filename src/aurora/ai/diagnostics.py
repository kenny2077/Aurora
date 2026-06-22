"""Safe connectivity checks for configured local AI providers."""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter

import httpx

from aurora.ai.client import AIClient, AIResponseFormatError
from aurora.config import AIConfig


@dataclass(frozen=True)
class AIProviderDiagnostic:
    """Provider health result suitable for CLI output and tests."""

    status: str
    detail: str
    latency_ms: int
    model_available: bool | None
    json_response_valid: bool | None


async def diagnose_ai_provider(
    config: AIConfig,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> AIProviderDiagnostic:
    """Fully validate a local provider without exposing URLs or credentials."""
    if not config.is_local_provider():
        return AIProviderDiagnostic(
            status="invalid_config",
            detail="configured provider is not local",
            latency_ms=0,
            model_available=None,
            json_response_valid=None,
        )
    if http_client is not None:
        return await _diagnose_with_client(config, http_client)
    async with httpx.AsyncClient(timeout=10.0) as client:
        return await _diagnose_with_client(config, client)


async def _diagnose_with_client(
    config: AIConfig,
    client: httpx.AsyncClient,
) -> AIProviderDiagnostic:
    started = perf_counter()
    try:
        if config.provider == "ollama":
            response = await client.get(f"{_ollama_root_url(config)}/api/tags")
            response.raise_for_status()
            names = {
                str(row.get("name") or "")
                for row in response.json().get("models", [])
                if isinstance(row, dict)
            }
            available = config.model in names
            status = "ok" if available else "model_missing"
            detail = "configured model is available" if available else "configured model is not installed"
        elif config.provider == "anythingllm":
            response = await client.get(f"{config.base_url.rstrip('/')}/api/docs")
            response.raise_for_status()
            available = None
            status = "ok"
            detail = "AnythingLLM API documentation is reachable"
        else:
            response = await client.get(f"{AIClient(config)._base_url()}/models")
            response.raise_for_status()
            available = _model_in_openai_response(response.json(), config.model)
            status = "ok" if available is not False else "model_missing"
            detail = "provider endpoint is reachable" if status == "ok" else "configured model is unavailable"
    except httpx.HTTPStatusError as exc:
        return _http_failure(config, exc, started, model_available=None)
    except (httpx.HTTPError, KeyError, TypeError, ValueError):
        return AIProviderDiagnostic(
            status="unavailable",
            detail="provider endpoint is unavailable or returned an invalid response",
            latency_ms=_elapsed_ms(started),
            model_available=None,
            json_response_valid=None,
        )

    if status != "ok":
        return AIProviderDiagnostic(
            status=status,
            detail=detail,
            latency_ms=_elapsed_ms(started),
            model_available=available,
            json_response_valid=None,
        )
    try:
        await AIClient(config, http_client=client).complete_json(
            "Return a JSON object with one numeric field named score.",
            "Return score 1.",
        )
        json_response_valid = True
    except httpx.HTTPStatusError as exc:
        return _http_failure(config, exc, started, model_available=available)
    except AIResponseFormatError:
        return AIProviderDiagnostic(
            status="invalid_response",
            detail="provider did not return Aurora-compatible JSON",
            latency_ms=_elapsed_ms(started),
            model_available=available,
            json_response_valid=False,
        )
    except (httpx.HTTPError, KeyError, TypeError, ValueError):
        return AIProviderDiagnostic(
            status="unavailable",
            detail="provider endpoint is unavailable or returned an invalid response",
            latency_ms=_elapsed_ms(started),
            model_available=available,
            json_response_valid=False,
        )
    return AIProviderDiagnostic(
        status="ok",
        detail=detail,
        latency_ms=_elapsed_ms(started),
        model_available=available,
        json_response_valid=True,
    )


def _ollama_root_url(config: AIConfig) -> str:
    base_url = AIClient(config)._base_url()
    return base_url[:-3] if base_url.endswith("/v1") else base_url


def _model_in_openai_response(payload: object, model: str) -> bool | None:
    if not isinstance(payload, dict):
        return None
    rows = payload.get("data")
    if not isinstance(rows, list):
        return None
    names = {str(row.get("id") or "") for row in rows if isinstance(row, dict)}
    return model in names


def _http_failure(
    config: AIConfig,
    exc: httpx.HTTPStatusError,
    started: float,
    *,
    model_available: bool | None,
) -> AIProviderDiagnostic:
    status_code = exc.response.status_code
    if status_code in {401, 403}:
        status = "authentication_failed"
        detail = "provider rejected configured authentication"
    elif config.provider == "anythingllm" and status_code == 404:
        status = "workspace_missing"
        detail = "configured AnythingLLM workspace is unavailable"
    else:
        status = "unavailable"
        detail = "provider endpoint is unavailable or rejected the request"
    return AIProviderDiagnostic(
        status=status,
        detail=detail,
        latency_ms=_elapsed_ms(started),
        model_available=model_available,
        json_response_valid=False,
    )


def _elapsed_ms(started: float) -> int:
    return round((perf_counter() - started) * 1000)
