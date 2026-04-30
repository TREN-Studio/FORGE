"""
FORGE x OpenAI Provider
=======================
OpenAI chat completions for BYOK users who want frontier fallback paths.
"""

from __future__ import annotations

import time
from typing import ClassVar

import httpx

from forge.core.models import ForgeResponse, Message, ModelSpec, ModelTier, TaskType
from forge.providers.base import BaseProvider

_API_URL = "https://api.openai.com/v1/chat/completions"


class OpenAIProvider(BaseProvider):
    name: ClassVar[str] = "openai"
    daily_token_limit: int = 0
    daily_request_limit: int = 0

    def __init__(
        self,
        api_key: str | None = None,
        config: dict[str, str] | None = None,
        allow_host_fallback: bool = True,
    ) -> None:
        super().__init__(api_key=api_key, config=config, allow_host_fallback=allow_host_fallback)
        self._organization = self._load_optional_value(
            "organization",
            ["OPENAI_ORGANIZATION", "OPENAI_ORG_ID"],
        )
        self._project = self._load_optional_value("project", ["OPENAI_PROJECT"])

    @property
    def models(self) -> list[ModelSpec]:
        return [
            ModelSpec(
                id="gpt-5.4-mini",
                provider="openai",
                display_name="GPT-5.4 Mini",
                tier=ModelTier.ULTRA,
                context_window=200_000,
                max_output_tokens=16_384,
                strong_at=[TaskType.CODE, TaskType.FAST, TaskType.GENERAL],
                tags=["coding", "reasoning", "multilingual"],
            ),
            ModelSpec(
                id="gpt-5.4",
                provider="openai",
                display_name="GPT-5.4",
                tier=ModelTier.ULTRA,
                context_window=400_000,
                max_output_tokens=16_384,
                supports_tools=True,
                strong_at=[TaskType.CODE, TaskType.REASONING, TaskType.RESEARCH],
                tags=["reasoning", "coding", "large-context"],
            ),
            ModelSpec(
                id="gpt-4o",
                provider="openai",
                display_name="GPT-4o",
                tier=ModelTier.PRO,
                context_window=128_000,
                max_output_tokens=16_384,
                supports_vision=True,
                supports_tools=True,
                strong_at=[TaskType.RESEARCH, TaskType.CREATIVE, TaskType.GENERAL],
                tags=["vision", "multilingual", "large-context"],
            ),
        ]

    @property
    def is_available(self) -> bool:
        return bool(self._api_key)

    async def complete(
        self,
        model: ModelSpec,
        messages: list[Message],
        max_tokens: int = 2048,
        temperature: float = 0.7,
    ) -> ForgeResponse:
        payload: dict[str, object] = {
            "model": model.id,
            "messages": [message.model_dump(exclude_none=True) for message in messages],
        }
        if model.id.startswith("o"):
            payload["max_completion_tokens"] = min(max_tokens, model.max_output_tokens)
        else:
            payload["max_tokens"] = min(max_tokens, model.max_output_tokens)
            payload["temperature"] = temperature

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        if self._organization:
            headers["OpenAI-Organization"] = self._organization
        if self._project:
            headers["OpenAI-Project"] = self._project

        started = time.monotonic()
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.post(_API_URL, json=payload, headers=headers)

        if resp.status_code == 429:
            raise RuntimeError("quota_exceeded: OpenAI rate limit hit")
        if resp.status_code != 200:
            raise RuntimeError(f"OpenAI API error {resp.status_code}: {resp.text[:200]}")

        data = resp.json()
        latency = (time.monotonic() - started) * 1000
        choice = data["choices"][0]
        usage = data.get("usage", {})

        return ForgeResponse(
            content=choice["message"].get("content", "") or "",
            model_id=model.id,
            provider="openai",
            latency_ms=latency,
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            finish_reason=choice.get("finish_reason", "stop"),
        )

    async def stream(
        self,
        model: ModelSpec,
        messages: list[Message],
        max_tokens: int = 2048,
        temperature: float = 0.7,
    ):
        payload: dict[str, object] = {
            "model": model.id,
            "messages": [message.model_dump(exclude_none=True) for message in messages],
        }
        if model.id.startswith("o"):
            payload["max_completion_tokens"] = min(max_tokens, model.max_output_tokens)
        else:
            payload["max_tokens"] = min(max_tokens, model.max_output_tokens)
            payload["temperature"] = temperature

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        if self._organization:
            headers["OpenAI-Organization"] = self._organization
        if self._project:
            headers["OpenAI-Project"] = self._project

        async for event in self._stream_openai_compatible(
            api_url=_API_URL,
            payload=payload,
            headers=headers,
            model=model,
            provider_name="openai",
            timeout=90,
        ):
            yield event
