"""
FORGE x Mistral Provider
========================
Mistral chat completions for general, research, and coding workloads.
"""

from __future__ import annotations

import time
from typing import ClassVar

import httpx

from forge.core.models import ForgeResponse, Message, ModelSpec, ModelTier, TaskType
from forge.providers.base import BaseProvider

_API_URL = "https://api.mistral.ai/v1/chat/completions"


class MistralProvider(BaseProvider):
    name: ClassVar[str] = "mistral"
    daily_token_limit: int = 0
    daily_request_limit: int = 0

    @property
    def models(self) -> list[ModelSpec]:
        return [
            ModelSpec(
                id="mistral-small-latest",
                provider="mistral",
                display_name="Mistral Small Latest",
                tier=ModelTier.PRO,
                context_window=131_072,
                max_output_tokens=8_192,
                supports_tools=True,
                strong_at=[TaskType.GENERAL, TaskType.RESEARCH],
                tags=["instruct", "large-context"],
            ),
            ModelSpec(
                id="mistral-medium-latest",
                provider="mistral",
                display_name="Mistral Medium Latest",
                tier=ModelTier.ULTRA,
                context_window=131_072,
                max_output_tokens=8_192,
                supports_tools=True,
                strong_at=[TaskType.RESEARCH, TaskType.CREATIVE, TaskType.GENERAL],
                tags=["instruct", "large-context", "creative"],
            ),
            ModelSpec(
                id="codestral-latest",
                provider="mistral",
                display_name="Codestral Latest",
                tier=ModelTier.ULTRA,
                context_window=262_144,
                max_output_tokens=8_192,
                strong_at=[TaskType.CODE, TaskType.REASONING],
                tags=["coding", "large-context", "instruct"],
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
            raise RuntimeError("quota_exceeded: Mistral rate limit hit")
        if resp.status_code != 200:
            raise RuntimeError(f"Mistral API error {resp.status_code}: {resp.text[:200]}")

        data = resp.json()
        latency = (time.monotonic() - started) * 1000
        choice = data["choices"][0]
        usage = data.get("usage", {})

        return ForgeResponse(
            content=choice["message"].get("content", "") or "",
            model_id=model.id,
            provider="mistral",
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
            provider_name="mistral",
            timeout=90,
        ):
            yield event
