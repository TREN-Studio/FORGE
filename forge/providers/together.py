"""
FORGE x Together Provider
=========================
Together chat completions for broad open-model fallback coverage.
"""

from __future__ import annotations

import time
from typing import ClassVar

import httpx

from forge.core.models import ForgeResponse, Message, ModelSpec, ModelTier, TaskType
from forge.providers.base import BaseProvider

_API_URL = "https://api.together.xyz/v1/chat/completions"


class TogetherProvider(BaseProvider):
    name: ClassVar[str] = "together"
    daily_token_limit: int = 0
    daily_request_limit: int = 0

    @property
    def models(self) -> list[ModelSpec]:
        return [
            ModelSpec(
                id="openai/gpt-oss-20b",
                provider="together",
                display_name="Together GPT-OSS 20B",
                tier=ModelTier.PRO,
                context_window=128_000,
                max_output_tokens=8_192,
                strong_at=[TaskType.FAST, TaskType.GENERAL],
                tags=["fast", "open-model"],
            ),
            ModelSpec(
                id="Qwen/Qwen2.5-7B-Instruct-Turbo",
                provider="together",
                display_name="Together Qwen 2.5 7B Instruct Turbo",
                tier=ModelTier.BASE,
                context_window=131_072,
                max_output_tokens=8_192,
                strong_at=[TaskType.FAST, TaskType.GENERAL, TaskType.CODE],
                tags=["fast", "coding", "instruct"],
            ),
            ModelSpec(
                id="meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo",
                provider="together",
                display_name="Together Llama 3.1 8B Instruct Turbo",
                tier=ModelTier.BASE,
                context_window=131_072,
                max_output_tokens=8_192,
                strong_at=[TaskType.GENERAL, TaskType.FAST],
                tags=["fast", "instruct", "multilingual"],
            ),
            ModelSpec(
                id="deepseek-ai/DeepSeek-R1-Distill-Llama-70B",
                provider="together",
                display_name="Together DeepSeek R1 Distill Llama 70B",
                tier=ModelTier.ULTRA,
                context_window=131_072,
                max_output_tokens=8_192,
                strong_at=[TaskType.REASONING, TaskType.MATH, TaskType.CODE],
                tags=["reasoning", "r1", "coding"],
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
            "max_tokens": min(max_tokens, model.max_output_tokens),
            "temperature": temperature,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        started = time.monotonic()
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.post(_API_URL, json=payload, headers=headers)

        if resp.status_code == 429:
            raise RuntimeError("quota_exceeded: Together rate limit hit")
        if resp.status_code != 200:
            raise RuntimeError(f"Together API error {resp.status_code}: {resp.text[:200]}")

        data = resp.json()
        latency = (time.monotonic() - started) * 1000
        choice = data["choices"][0]
        usage = data.get("usage", {})

        return ForgeResponse(
            content=choice["message"].get("content", "") or "",
            model_id=model.id,
            provider="together",
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
        payload = {
            "model": model.id,
            "messages": [message.model_dump(exclude_none=True) for message in messages],
            "max_tokens": min(max_tokens, model.max_output_tokens),
            "temperature": temperature,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        async for event in self._stream_openai_compatible(
            api_url=_API_URL,
            payload=payload,
            headers=headers,
            model=model,
            provider_name="together",
            timeout=90,
        ):
            yield event
