"""Provider-neutral async AI client for Aurora JSON analysis."""

from __future__ import annotations

import os
from typing import Any

import httpx

from aurora.ai.json import parse_json_object
from aurora.config import AIConfig


DEFAULT_BASE_URLS = {
    "deepseek": "https://api.deepseek.com/v1",
    "openai": "https://api.openai.com/v1",
}


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
        return bool(os.getenv(self.config.api_key_env))

    async def complete_json(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        if not self.is_configured():
            raise RuntimeError(f"missing AI API key env var: {self.config.api_key_env}")
        if self.http_client is not None:
            return await self._complete_with_client(self.http_client, system_prompt, user_prompt)
        async with httpx.AsyncClient(timeout=60.0) as client:
            return await self._complete_with_client(client, system_prompt, user_prompt)

    async def _complete_with_client(
        self,
        client: httpx.AsyncClient,
        system_prompt: str,
        user_prompt: str,
    ) -> dict[str, Any]:
        response = await client.post(
            f"{self._base_url()}/chat/completions",
            headers={
                "Authorization": f"Bearer {os.environ[self.config.api_key_env]}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.config.model,
                "temperature": self.config.temperature,
                "max_tokens": self.config.max_tokens,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "response_format": {"type": "json_object"},
            },
        )
        response.raise_for_status()
        payload = response.json()
        content = payload["choices"][0]["message"]["content"]
        return parse_json_object(str(content))

    def _base_url(self) -> str:
        return (self.config.base_url or DEFAULT_BASE_URLS.get(self.config.provider, "")).rstrip("/")
