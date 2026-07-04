"""Provider-neutral async AI client for Aurora JSON analysis."""

from __future__ import annotations

import os
from typing import Any

import httpx

from aurora.ai.providers import AIResponseFormatError, AnythingLLMProvider, OpenAICompatibleProvider
from aurora.config import AIConfig


DEFAULT_BASE_URLS = {
    "deepseek": "https://api.deepseek.com/v1",
    "openai": "https://api.openai.com/v1",
    "ollama": "http://127.0.0.1:11434/v1",
    "lmstudio": "http://127.0.0.1:1234/v1",
}
LOCAL_OPTIONAL_AUTH_PROVIDERS = frozenset({"ollama", "lmstudio", "openai_compatible"})


class AIClient:
    """Small chat-completions client that returns strict JSON objects."""

    def __init__(
        self,
        config: AIConfig,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self.http_client = http_client

    def is_configured(self) -> bool:
        if self.config.provider in LOCAL_OPTIONAL_AUTH_PROVIDERS:
            return True
        return bool(os.getenv(self.config.api_key_env))

    async def complete_json(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        if not self.is_configured():
            raise RuntimeError(f"missing AI API key env var: {self.config.api_key_env}")
        if self.http_client is not None:
            return await self._complete_with_client(self.http_client, system_prompt, user_prompt)
        async with httpx.AsyncClient(timeout=self.config.request_timeout_sec) as client:
            return await self._complete_with_client(client, system_prompt, user_prompt)

    async def _complete_with_client(
        self,
        client: httpx.AsyncClient,
        system_prompt: str,
        user_prompt: str,
    ) -> dict[str, Any]:
        if self.config.provider == "anythingllm":
            return await AnythingLLMProvider(self.config, self._base_url()).complete_json(
                client, system_prompt, user_prompt
            )
        headers = {"Content-Type": "application/json"}
        headers.update(self._authorization_headers())
        return await OpenAICompatibleProvider(
            self.config, self._base_url(), headers
        ).complete_json(client, system_prompt, user_prompt)

    def _authorization_headers(self) -> dict[str, str]:
        token = os.getenv(self.config.api_key_env)
        if not token:
            return {}
        if (
            self.config.provider in LOCAL_OPTIONAL_AUTH_PROVIDERS
            and self.config.api_key_env == "DEEPSEEK_API_KEY"
        ):
            return {}
        return {"Authorization": f"Bearer {token}"}

    def _base_url(self) -> str:
        base_url = self.config.base_url or DEFAULT_BASE_URLS.get(self.config.provider)
        if not base_url:
            raise RuntimeError(f"missing base URL for AI provider: {self.config.provider}")
        return base_url.rstrip("/")
