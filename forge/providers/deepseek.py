"""
FORGE x DeepSeek Provider
==========================
DeepSeek gives FORGE a dedicated reasoning lane using the official
OpenAI-compatible chat completions API.
"""

from __future__ import annotations

import time
from typing import ClassVar

import httpx

from forge.core.models import ForgeResponse, Message, ModelSpec, ModelTier, TaskType
from forge.providers.base import BaseProvider

_API_URL = "https://api.deepseek.com/chat/completions"


class DeepSeekProvider(BaseProvider):
    name: ClassVar[str] = "deepseek"
    daily_token_limit: int = 500_000
    daily_request_limit: int = 50

    @property
    def models(self) -> list[ModelSpec]:
        return [
            ModelSpec(
                id="deepseek-chat",
                provider="deepseek",
                display_name="DeepSeek Chat",
                tier=ModelTier.PRO,
                context_window=64_000,
                max_output_tokens=8_192,
                supports_tools=True,
                strong_at=[TaskType.CODE, TaskType.GENERAL, TaskType.RESEARCH],
                tags=["instruct", "coding", "large-context"],
            ),
            ModelSpec(
                id="deepseek-reasoner",
                provider="deepseek",
                display_name="DeepSeek Reasoner",
                tier=ModelTier.ULTRA,
                context_window=64_000,
                max_output_tokens=8_192,
                supports_tools=True,
                strong_at=[TaskType.REASONING, TaskType.MATH, TaskType.CODE],
                tags=["reasoning", "r1", "instruct"],
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
        payload: dict = {
            "model": model.id,
            "messages": [message.model_dump(exclude_none=True) for message in messages],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if model.id == "deepseek-reasoner":
            payload["thinking"] = {"type": "enabled"}

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        started = time.monotonic()
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.post(_API_URL, json=payload, headers=headers)

        if resp.status_code == 429:
            raise RuntimeError("quota_exceeded: DeepSeek rate limit hit")
        if resp.status_code != 200:
            raise RuntimeError(f"DeepSeek API error {resp.status_code}: {resp.text[:200]}")

        data = resp.json()
        latency = (time.monotonic() - started) * 1000
        choice = data["choices"][0]
        usage = data.get("usage", {})

        return ForgeResponse(
            content=choice["message"].get("content", "") or "",
            model_id=model.id,
            provider="deepseek",
            latency_ms=latency,
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            finish_reason=choice.get("finish_reason", "stop"),
        )
