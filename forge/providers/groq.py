"""
FORGE × Groq Provider
======================
Groq runs LLaMA 3.3 70B at 500+ tokens/second.
For free. This is the backbone of FORGE's speed.

Free tier: 500K tokens/day · 14,400 requests/day
Sign up  : https://console.groq.com (takes 30 seconds)
"""

from __future__ import annotations

import time
from typing import ClassVar

import httpx

from forge.core.models import (
    ForgeResponse,
    Message,
    ModelSpec,
    ModelTier,
    TaskType,
)
from forge.providers.base import BaseProvider

_API_URL = "https://api.groq.com/openai/v1/chat/completions"


class GroqProvider(BaseProvider):
    """
    Groq Cloud — fastest free LLM inference available.
    Uses the OpenAI-compatible endpoint.
    """

    name: ClassVar[str] = "groq"
    daily_token_limit:   int = 500_000
    daily_request_limit: int = 14_400

    @property
    def models(self) -> list[ModelSpec]:
        # Catalog verified live against GET /openai/v1/models on 2026-09-10.
        # Groq retired the entire llama-3.x line; current chat lineup:
        # gpt-oss-120b/20b + qwen3.6/3.8 + groq/compound agentic models.
        return [
            ModelSpec(
                id="openai/gpt-oss-120b",
                provider="groq",
                display_name="GPT-OSS 120B (Groq)",
                tier=ModelTier.ULTRA,
                context_window=131_072,
                max_output_tokens=32_768,
                supports_tools=True,
                strong_at=[TaskType.CODE, TaskType.REASONING, TaskType.GENERAL],
                tags=["instruct", "fast", "coding"],
            ),
            ModelSpec(
                id="qwen/qwen3.8-27b",
                provider="groq",
                display_name="Qwen 3.8 27B (Groq)",
                tier=ModelTier.ULTRA,
                context_window=131_072,
                max_output_tokens=32_768,
                supports_tools=True,
                strong_at=[TaskType.CODE, TaskType.REASONING, TaskType.GENERAL],
                tags=["instruct", "coding", "fast", "multilingual", "arabic"],
            ),
            ModelSpec(
                id="qwen/qwen3.6-27b",
                provider="groq",
                display_name="Qwen 3.6 27B (Groq, preview)",
                tier=ModelTier.ULTRA,
                context_window=131_072,
                max_output_tokens=32_768,
                supports_tools=True,
                strong_at=[TaskType.CODE, TaskType.REASONING, TaskType.GENERAL],
                tags=["instruct", "coding", "fast", "multilingual", "arabic"],
            ),
            ModelSpec(
                id="openai/gpt-oss-20b",
                provider="groq",
                display_name="GPT-OSS 20B (Groq)",
                tier=ModelTier.PRO,
                context_window=131_072,
                max_output_tokens=8_192,
                supports_tools=True,
                strong_at=[TaskType.GENERAL, TaskType.FAST],
                tags=["instruct", "fast"],
            ),
            ModelSpec(
                id="groq/compound",
                provider="groq",
                display_name="Groq Compound (agentic)",
                tier=ModelTier.PRO,
                context_window=131_072,
                max_output_tokens=32_768,
                supports_tools=True,
                strong_at=[TaskType.GENERAL, TaskType.RESEARCH],
                tags=["instruct", "agentic", "tools"],
            ),
        ]

    @property
    def is_available(self) -> bool:
        return bool(self._api_key)

    async def complete(
        self,
        model:       ModelSpec,
        messages:    list[Message],
        max_tokens:  int   = 2048,
        temperature: float = 0.7,
    ) -> ForgeResponse:
        payload = {
            "model":       model.id,
            "messages":    [m.model_dump(exclude_none=True) for m in messages],
            "max_tokens":  max_tokens,
            "temperature": temperature,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type":  "application/json",
        }

        t0 = time.monotonic()
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(_API_URL, json=payload, headers=headers)

        if resp.status_code == 429:
            raise RuntimeError("quota_exceeded: Groq rate limit hit")
        if resp.status_code != 200:
            raise RuntimeError(f"Groq API error {resp.status_code}: {resp.text[:200]}")

        data    = resp.json()
        latency = (time.monotonic() - t0) * 1000
        choice  = data["choices"][0]
        usage   = data.get("usage", {})

        return ForgeResponse(
            content       = choice["message"]["content"] or "",
            model_id      = model.id,
            provider      = "groq",
            latency_ms    = latency,
            input_tokens  = usage.get("prompt_tokens", 0),
            output_tokens = usage.get("completion_tokens", 0),
            finish_reason = choice.get("finish_reason", "stop"),
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
            "messages": [m.model_dump(exclude_none=True) for m in messages],
            "max_tokens": max_tokens,
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
            provider_name="groq",
            timeout=60,
        ):
            yield event
