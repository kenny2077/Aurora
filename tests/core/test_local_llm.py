from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from pydantic import ValidationError

from aurora.ai.client import AIClient
from aurora.ai.client import AIResponseFormatError
from aurora.ai.diagnostics import diagnose_ai_provider
from aurora.ai.ranker import LLMRanker
from aurora.config import AIConfig
from aurora.config import FinalScoreWeights
from aurora.models import SignalItem
from aurora.pipeline import StageContext


def test_ollama_accepts_any_model_tag_and_needs_no_api_key() -> None:
    config = AIConfig(provider="ollama", model="llama3.2:3b")

    assert config.model_for_task("ranking") == "llama3.2:3b"
    assert AIClient(config).is_configured() is True


def test_task_model_override_is_selected_for_repair() -> None:
    config = AIConfig(
        provider="ollama",
        model="qwen2.5:3b",
        task_models={"repair": "mistral:7b"},
    )

    assert config.model_for_task("ranking") == "qwen2.5:3b"
    assert config.model_for_task("repair") == "mistral:7b"


def test_local_only_rejects_cloud_provider() -> None:
    with pytest.raises(ValidationError, match="local_only"):
        AIConfig(local_only=True)


def test_local_doctor_reports_cloud_provider_as_invalid_configuration() -> None:
    diagnostic = asyncio.run(diagnose_ai_provider(AIConfig()))

    assert diagnostic.status == "invalid_config"


def test_openai_compatible_requires_explicit_base_url() -> None:
    with pytest.raises(ValidationError, match="openai_compatible requires ai.base_url"):
        AIConfig(provider="openai_compatible", model="local-model")


def test_ollama_uses_openai_compatible_endpoint_without_auth_header() -> None:
    observed: dict[str, object] = {}

    async def run() -> dict:
        def handler(request: httpx.Request) -> httpx.Response:
            observed["url"] = str(request.url)
            observed["authorization"] = request.headers.get("Authorization")
            observed["body"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": '{"score": 8}'}}]},
            )

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http_client:
            client = AIClient(AIConfig(provider="ollama", model="qwen2.5:3b"), http_client=http_client)
            return await client.complete_json("system", "user")

    assert asyncio.run(run()) == {"score": 8}
    assert observed["url"] == "http://127.0.0.1:11434/v1/chat/completions"
    assert observed["authorization"] is None
    assert observed["body"] == {
        "model": "qwen2.5:3b",
        "temperature": 0.2,
        "max_tokens": 4096,
        "messages": [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "user"},
        ],
        "response_format": {"type": "json_object"},
    }


@pytest.mark.parametrize(
    ("provider", "base_url", "expected_url"),
    [
        ("lmstudio", None, "http://127.0.0.1:1234/v1/chat/completions"),
        ("openai_compatible", "http://127.0.0.1:8080/v1", "http://127.0.0.1:8080/v1/chat/completions"),
    ],
)
def test_openai_compatible_local_providers_use_configured_or_default_endpoint(
    provider: str,
    base_url: str | None,
    expected_url: str,
) -> None:
    observed: list[str] = []

    async def capture() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            observed.append(str(request.url))
            assert request.headers.get("Authorization") is None
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": '{"score": 8}'}}]},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            await AIClient(
                AIConfig(provider=provider, model="local-model", base_url=base_url),  # type: ignore[arg-type]
                http_client=http_client,
            ).complete_json("system", "user")

    asyncio.run(capture())
    assert observed == [expected_url]


def test_malformed_provider_envelope_is_reported_as_json_format_error() -> None:
    async def run() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"choices": []}))
        ) as http_client:
            with pytest.raises(AIResponseFormatError, match="response format"):
                await AIClient(
                    AIConfig(provider="ollama", model="qwen2.5:3b"),
                    http_client=http_client,
                ).complete_json("system", "user")

    asyncio.run(run())


def test_anythingllm_workspace_response_uses_strict_json_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: dict[str, object] = {}
    monkeypatch.setenv("ANYTHINGLLM_TEST_KEY", "test-key")

    async def run() -> dict:
        def handler(request: httpx.Request) -> httpx.Response:
            observed["url"] = str(request.url)
            observed["authorization"] = request.headers.get("Authorization")
            observed["body"] = json.loads(request.content)
            return httpx.Response(200, json={"textResponse": '{"score": 9}'})

        transport = httpx.MockTransport(handler)
        config = AIConfig(
            provider="anythingllm",
            model="workspace-default",
            base_url="http://127.0.0.1:3001",
            api_key_env="ANYTHINGLLM_TEST_KEY",
            workspace_slug="aurora",
        )
        async with httpx.AsyncClient(transport=transport) as http_client:
            return await AIClient(config, http_client=http_client).complete_json("system", "user")

    assert asyncio.run(run()) == {"score": 9}
    assert observed["url"] == "http://127.0.0.1:3001/api/v1/workspace/aurora/chat"
    assert observed["authorization"] == "Bearer test-key"
    assert observed["body"] == {"message": "system\n\nuser", "mode": "chat"}


def test_anythingllm_malformed_response_uses_json_format_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANYTHINGLLM_TEST_KEY", "test-key")

    async def run() -> None:
        config = AIConfig(
            provider="anythingllm",
            model="workspace-default",
            base_url="http://127.0.0.1:3001",
            api_key_env="ANYTHINGLLM_TEST_KEY",
            workspace_slug="aurora",
        )
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json={"textResponse": "not JSON"})
            )
        ) as http_client:
            with pytest.raises(AIResponseFormatError):
                await AIClient(config, http_client=http_client).complete_json("system", "user")

    asyncio.run(run())


def test_ollama_diagnostic_reports_missing_configured_model() -> None:
    async def run():
        def handler(request: httpx.Request) -> httpx.Response:
            assert str(request.url) == "http://127.0.0.1:11434/api/tags"
            return httpx.Response(200, json={"models": [{"name": "qwen2.5:3b"}]})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            return await diagnose_ai_provider(
                AIConfig(provider="ollama", model="llama3.2:3b"),
                http_client=http_client,
            )

    diagnostic = asyncio.run(run())

    assert diagnostic.status == "model_missing"
    assert diagnostic.model_available is False
    assert diagnostic.latency_ms >= 0


def test_local_doctor_validates_json_response_by_default() -> None:
    async def run():
        requests: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(str(request.url))
            if request.url.path == "/api/tags":
                return httpx.Response(200, json={"models": [{"name": "qwen2.5:3b"}]})
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": '{"score": 1}'}}]},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            diagnostic = await diagnose_ai_provider(
                AIConfig(provider="ollama", model="qwen2.5:3b"),
                http_client=http_client,
            )
        return diagnostic, requests

    diagnostic, requests = asyncio.run(run())

    assert diagnostic.status == "ok"
    assert diagnostic.json_response_valid is True
    assert requests == [
        "http://127.0.0.1:11434/api/tags",
        "http://127.0.0.1:11434/v1/chat/completions",
    ]


def test_anythingllm_doctor_reports_authentication_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANYTHINGLLM_TEST_KEY", "test-key")

    async def run():
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/docs":
                return httpx.Response(200, json={})
            return httpx.Response(401, json={"error": "invalid token"})

        config = AIConfig(
            provider="anythingllm",
            model="workspace-default",
            base_url="http://127.0.0.1:3001",
            api_key_env="ANYTHINGLLM_TEST_KEY",
            workspace_slug="aurora",
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            return await diagnose_ai_provider(config, http_client=http_client)

    diagnostic = asyncio.run(run())

    assert diagnostic.status == "authentication_failed"
    assert "token" not in diagnostic.detail


def test_anythingllm_doctor_reports_missing_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANYTHINGLLM_TEST_KEY", "test-key")

    async def run():
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/docs":
                return httpx.Response(200, json={})
            return httpx.Response(404, json={"error": "workspace missing"})

        config = AIConfig(
            provider="anythingllm",
            model="workspace-default",
            base_url="http://127.0.0.1:3001",
            api_key_env="ANYTHINGLLM_TEST_KEY",
            workspace_slug="aurora",
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            return await diagnose_ai_provider(config, http_client=http_client)

    assert asyncio.run(run()).status == "workspace_missing"


def test_local_doctor_reports_invalid_json_without_returning_provider_text() -> None:
    async def run():
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/tags":
                return httpx.Response(200, json={"models": [{"name": "qwen2.5:3b"}]})
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "not JSON token=secret"}}]},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            return await diagnose_ai_provider(
                AIConfig(provider="ollama", model="qwen2.5:3b"),
                http_client=http_client,
            )

    diagnostic = asyncio.run(run())

    assert diagnostic.status == "invalid_response"
    assert "secret" not in diagnostic.detail


def test_cloud_usage_calculates_cost_from_configured_token_rates() -> None:
    item = SignalItem(
        id="news:cost",
        type="news",
        title="Cost",
        url="https://example.com/cost",
        source="test",
        published_at="2026-06-21T00:00:00Z",
        deterministic_score=7.0,
        final_score=7.0,
    )
    ranker = LLMRanker(
        AIConfig(
            provider="deepseek",
            api_key_env="AURORA_TEST_KEY",
            input_cost_per_million_tokens=2.0,
            output_cost_per_million_tokens=4.0,
        ),
        weights=FinalScoreWeights(),
        client=_ValidAnalysisClient(),
    )
    context = StageContext(mode="test", run_id="cost")

    asyncio.run(ranker.analyze_items([item], lambda _: ("system", "user"), context))

    usage = context.metadata["ai_usage"]
    expected = (
        usage["approx_prompt_tokens"] * 2.0 + usage["approx_completion_tokens"] * 4.0
    ) / 1_000_000
    assert usage["estimated_cloud_cost_usd"] == pytest.approx(expected)


def test_ranker_records_local_task_model_and_json_fallback() -> None:
    item = SignalItem(
        id="news:1",
        type="news",
        title="News",
        url="https://example.com/news",
        source="test",
        published_at="2026-06-20T00:00:00Z",
        deterministic_score=7.0,
        final_score=7.0,
    )
    ranker = LLMRanker(
        AIConfig(
            provider="ollama",
            model="qwen2.5:3b",
            task_models={"repair": "mistral:7b"},
        ),
        weights=FinalScoreWeights(),
        client=_InvalidJsonClient(),
        task="repair",
    )
    context = StageContext(mode="test", run_id="local")

    analyses = asyncio.run(ranker.analyze_items([item], lambda _: ("system", "user"), context))

    assert analyses == {}
    assert context.metadata["ai_usage"] == {
        "provider": "ollama",
        "model": "mistral:7b",
        "endpoint_kind": "local",
        "local_only": False,
        "task_models": {"repair": "mistral:7b"},
        "requested_calls": 1,
        "network_attempts": 1,
        "retried_calls": 0,
        "succeeded_calls": 0,
        "failed_calls": 1,
        "skipped_by_budget": 0,
        "approx_prompt_tokens": context.metadata["ai_usage"]["approx_prompt_tokens"],
        "approx_completion_tokens": 0,
        "approx_total_tokens": context.metadata["ai_usage"]["approx_total_tokens"],
        "latency_ms_total": context.metadata["ai_usage"]["latency_ms_total"],
        "json_failures": 1,
        "deterministic_fallbacks": 1,
        "failure_categories": {"invalid_response": 1},
        "estimated_cloud_cost_usd": 0.0,
    }


class _InvalidJsonClient:
    def is_configured(self) -> bool:
        return True

    async def complete_json(self, system_prompt: str, user_prompt: str) -> dict:
        from aurora.ai.client import AIResponseFormatError

        raise AIResponseFormatError("invalid response")


class _ValidAnalysisClient:
    def is_configured(self) -> bool:
        return True

    async def complete_json(self, system_prompt: str, user_prompt: str) -> dict:
        return {"score": 8, "summary": "A concise local summary."}
