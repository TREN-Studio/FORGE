"""
FORGE x OpenRouter Provider
============================
OpenRouter gives FORGE broad fallback coverage through a single
OpenAI-compatible endpoint that exposes many free models.
"""

from __future__ import annotations

import time
from typing import ClassVar

import httpx

from forge.core.models import ForgeResponse, Message, ModelSpec, ModelTier, TaskType
from forge.providers.base import BaseProvider

_API_URL = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouterProvider(BaseProvider):
    name: ClassVar[str] = "openrouter"
    daily_token_limit: int = 200_000
    daily_request_limit: int = 0

    @property
    def models(self) -> list[ModelSpec]:
        return [
            ModelSpec(
                id="deepseek/deepseek-r1:free",
                provider="openrouter",
                display_name="DeepSeek R1 Free",
                tier=ModelTier.ULTRA,
                context_window=64_000,
                max_output_tokens=8_192,
                supports_tools=True,
                strong_at=[TaskType.REASONING, TaskType.MATH, TaskType.CODE],
                tags=["reasoning", "r1", "free"],
            ),
            ModelSpec(
                id="meta-llama/llama-3.3-70b-instruct:free",
                provider="openrouter",
                display_name="LLaMA 3.3 70B Free",
                tier=ModelTier.ULTRA,
                context_window=131_072,
                max_output_tokens=8_192,
                supports_tools=True,
                strong_at=[TaskType.GENERAL, TaskType.CODE, TaskType.RESEARCH],
                tags=["instruct", "free", "large-context"],
            ),
            ModelSpec(
                id="qwen/qwen-2.5-72b-instruct:free",
                provider="openrouter",
                display_name="Qwen 2.5 72B Free",
                tier=ModelTier.ULTRA,
                context_window=32_768,
                max_output_tokens=8_192,
                supports_tools=True,
                strong_at=[TaskType.CODE, TaskType.GENERAL],
                tags=["coding", "instruct", "free"],
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
        payload = {
            "model": model.id,
            "messages": [message.model_dump(exclude_none=True) for message in messages],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://www.trenstudio.com/FORGE",
            "X-Title": "FORGE",
        }

        started = time.monotonic()
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.post(_API_URL, json=payload, headers=headers)

        if resp.status_code == 429:
            raise RuntimeError("quota_exceeded: OpenRouter rate limit hit")
        if resp.status_code != 200:
            raise RuntimeError(f"OpenRouter API error {resp.status_code}: {resp.text[:200]}")

        data = resp.json()
        latency = (time.monotonic() - started) * 1000
        choice = data["choices"][0]
        usage = data.get("usage", {})

        return ForgeResponse(
            content=choice["message"].get("content", "") or "",
            model_id=model.id,
            provider="openrouter",
            latency_ms=latency,
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            finish_reason=choice.get("finish_reason", "stop"),
        )
