"""
FORGE x NVIDIA NIM Provider
===========================
NVIDIA-hosted NIM chat completions for coding, reasoning, and fallback coverage.
"""

from __future__ import annotations

import time
from typing import ClassVar

import httpx

from forge.core.models import ForgeResponse, Message, ModelSpec, ModelTier, TaskType
from forge.providers.base import BaseProvider

_API_URL = "https://integrate.api.nvidia.com/v1/chat/completions"


class NvidiaProvider(BaseProvider):
    name: ClassVar[str] = "nvidia"
    daily_token_limit: int = 0
    daily_request_limit: int = 0

    @property
    def models(self) -> list[ModelSpec]:
        return [
            ModelSpec(
                id="qwen/qwen3-coder-480b-a35b-instruct",
                provider="nvidia",
                display_name="NVIDIA NIM Qwen3 Coder 480B A35B",
                tier=ModelTier.ULTRA,
                context_window=262_144,
                max_output_tokens=8_192,
                supports_tools=True,
                strong_at=[TaskType.CODE, TaskType.REASONING, TaskType.RESEARCH],
                tags=["coding", "reasoning", "large-context"],
            ),
            ModelSpec(
                id="deepseek-ai/deepseek-v3.2",
                provider="nvidia",
                display_name="NVIDIA NIM DeepSeek V3.2",
                tier=ModelTier.ULTRA,
                context_window=128_000,
                max_output_tokens=8_192,
                supports_tools=True,
                strong_at=[TaskType.REASONING, TaskType.GENERAL, TaskType.RESEARCH],
                tags=["reasoning", "large-context", "instruct"],
            ),
            ModelSpec(
                id="meta/llama-3.1-405b-instruct",
                provider="nvidia",
                display_name="NVIDIA NIM Llama 3.1 405B Instruct",
                tier=ModelTier.ULTRA,
                context_window=128_000,
                max_output_tokens=8_192,
                supports_tools=True,
                strong_at=[TaskType.GENERAL, TaskType.RESEARCH, TaskType.CREATIVE],
                tags=["large-context", "instruct", "multilingual"],
            ),
            ModelSpec(
                id="openai/gpt-oss-120b",
                provider="nvidia",
                display_name="NVIDIA NIM GPT-OSS 120B",
                tier=ModelTier.ULTRA,
                context_window=128_000,
                max_output_tokens=8_192,
                supports_tools=True,
                strong_at=[TaskType.CODE, TaskType.GENERAL, TaskType.FAST],
                tags=["coding", "open-model", "instruct"],
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
            raise RuntimeError("quota_exceeded: NVIDIA NIM rate limit hit")
        if resp.status_code != 200:
            raise RuntimeError(f"NVIDIA NIM API error {resp.status_code}: {resp.text[:200]}")

        data = resp.json()
        latency = (time.monotonic() - started) * 1000
        choice = data["choices"][0]
        usage = data.get("usage", {})

        return ForgeResponse(
            content=choice["message"].get("content", "") or "",
            model_id=model.id,
            provider="nvidia",
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
            provider_name="nvidia",
            timeout=90,
        ):
            yield event
