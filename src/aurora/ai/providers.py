"""Provider-specific JSON completion adapters."""

from __future__ import annotations

import os
from typing import Any

import httpx

from aurora.ai.json import parse_json_object
from aurora.config import AIConfig


class AIResponseFormatError(ValueError):
    """Raised when an LLM response cannot satisfy Aurora's JSON contract."""


class OpenAICompatibleProvider:
    """Chat-completions adapter for OpenAI, Ollama, LM Studio, and compatible APIs."""

    def __init__(self, config: AIConfig, base_url: str, headers: dict[str, str]) -> None:
        self.config = config
        self.base_url = base_url
        self.headers = headers

    async def complete_json(
        self,
        client: httpx.AsyncClient,
        system_prompt: str,
        user_prompt: str,
    ) -> dict[str, Any]:
        content = await self._chat_completion(
            client,
            system_prompt,
            user_prompt,
            json_mode=True,
        )
        if not content.strip():
            content = await self._chat_completion(
                client,
                system_prompt,
                user_prompt,
                json_mode=True,
            )
        try:
            return _parse_response(content)
        except AIResponseFormatError:
            content = await self._chat_completion(
                client,
                (
                    f"{system_prompt}\n\nReturn only valid JSON. Do not include prose, "
                    "Markdown fences, or explanation."
                ),
                user_prompt,
                json_mode=False,
            )
            return _parse_response(content)

    async def _chat_completion(
        self,
        client: httpx.AsyncClient,
        system_prompt: str,
        user_prompt: str,
        *,
        json_mode: bool,
    ) -> str:
        body: dict[str, Any] = {
            "model": self.config.model,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        response = await client.post(
            f"{self.base_url}/chat/completions",
            headers=self.headers,
            json=body,
        )
        response.raise_for_status()
        try:
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            raise AIResponseFormatError("AI response format is invalid") from exc
        return str(content or "")


class AnythingLLMProvider:
    """Workspace-chat adapter for AnythingLLM's developer API."""

    def __init__(self, config: AIConfig, base_url: str) -> None:
        self.config = config
        self.base_url = base_url

    async def complete_json(
        self,
        client: httpx.AsyncClient,
        system_prompt: str,
        user_prompt: str,
    ) -> dict[str, Any]:
        workspace_slug = self.config.workspace_slug
        if not workspace_slug:
            raise RuntimeError("anythingllm requires ai.workspace_slug")
        response = await client.post(
            f"{self.base_url}/api/v1/workspace/{workspace_slug}/chat",
            headers={
                "Authorization": f"Bearer {os.environ[self.config.api_key_env]}",
                "Content-Type": "application/json",
            },
            json={
                "message": f"{system_prompt}\n\n{user_prompt}",
                "mode": self.config.anythingllm_mode,
            },
        )
        response.raise_for_status()
        try:
            payload = response.json()
            content = payload["textResponse"]
        except (KeyError, TypeError, ValueError) as exc:
            raise AIResponseFormatError("AI response format is invalid") from exc
        return _parse_response(str(content))


def _parse_response(content: str) -> dict[str, Any]:
    try:
        return parse_json_object(content)
    except (TypeError, ValueError) as exc:
        raise AIResponseFormatError("AI response did not contain a JSON object") from exc
