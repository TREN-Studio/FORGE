"""
FORGE × Hugging Face Provider
=============================
Free serverless inference API for popular open-source models.
"""

from __future__ import annotations

import time
from typing import ClassVar

from forge.core.models import (
    ForgeResponse,
    Message,
    ModelSpec,
    ModelTier,
    TaskType,
)
from forge.providers.base import BaseProvider

_API_URL = "https://api-inference.huggingface.co/v1/chat/completions"


class HuggingFaceProvider(BaseProvider):
    """Hugging Face Serverless Inference API — completely free open models."""

    name: ClassVar[str] = "huggingface"
    daily_token_limit:   int = 1_000_000
    daily_request_limit: int = 1_000

    @property
    def models(self) -> list[ModelSpec]:
        return [
            ModelSpec(
                id="meta-llama/Llama-3.3-70B-Instruct",
                provider="huggingface",
                display_name="LLaMA 3.3 70B (HuggingFace)",
                tier=ModelTier.ULTRA,
                context_window=131_072,
                max_output_tokens=8_192,
                supports_tools=True,
                strong_at=[TaskType.CODE, TaskType.REASONING, TaskType.RESEARCH],
                tags=["instruct", "large-context", "free"],
            ),
            ModelSpec(
                id="Qwen/Qwen2.5-Coder-32B-Instruct",
                provider="huggingface",
                display_name="Qwen 2.5 Coder 32B (HuggingFace)",
                tier=ModelTier.PRO,
                context_window=32_768,
                max_output_tokens=8_192,
                supports_tools=True,
                strong_at=[TaskType.CODE, TaskType.FAST],
                tags=["coding", "fast", "free"],
            ),
            ModelSpec(
                id="mistralai/Mistral-7B-Instruct-v0.3",
                provider="huggingface",
                display_name="Mistral 7B (HuggingFace)",
                tier=ModelTier.BASE,
                context_window=32_768,
                max_output_tokens=8_192,
                strong_at=[TaskType.FAST, TaskType.GENERAL],
                tags=["fast", "free"],
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
            "max_tokens":  min(max_tokens, model.max_output_tokens),
            "temperature": temperature,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type":  "application/json",
        }

        t0 = time.monotonic()
        import httpx
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(_API_URL, json=payload, headers=headers)

        if resp.status_code == 429:
            raise RuntimeError("quota_exceeded: HuggingFace rate limit hit")
        if resp.status_code != 200:
            raise RuntimeError(f"HuggingFace API error {resp.status_code}: {resp.text[:200]}")

        data    = resp.json()
        latency = (time.monotonic() - t0) * 1000
        choice  = data["choices"][0]
        usage   = data.get("usage", {})

        return ForgeResponse(
            content       = choice["message"]["content"] or "",
            model_id      = model.id,
            provider      = "huggingface",
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
            provider_name="huggingface",
            timeout=60,
        ):
            yield event
