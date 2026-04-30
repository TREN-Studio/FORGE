"""
FORGE x Anthropic Provider
==========================
Claude via the Anthropic Messages API.
"""

from __future__ import annotations

import time
from typing import ClassVar

import httpx

from forge.core.models import ForgeResponse, Message, ModelSpec, ModelTier, TaskType
from forge.providers.base import BaseProvider

_API_URL = "https://api.anthropic.com/v1/messages"


class AnthropicProvider(BaseProvider):
    name: ClassVar[str] = "anthropic"
    daily_token_limit: int = 0
    daily_request_limit: int = 0

    def __init__(
        self,
        api_key: str | None = None,
        config: dict[str, str] | None = None,
        allow_host_fallback: bool = True,
    ) -> None:
        super().__init__(api_key=api_key, config=config, allow_host_fallback=allow_host_fallback)

    @property
    def models(self) -> list[ModelSpec]:
        return [
            ModelSpec(
                id="claude-haiku-4-5",
                provider="anthropic",
                display_name="Claude Haiku 4.5",
                tier=ModelTier.FAST,
                context_window=200_000,
                max_output_tokens=8_192,
                strong_at=[TaskType.FAST, TaskType.GENERAL],
                tags=["fast", "instruct", "multilingual"],
            ),
            ModelSpec(
                id="claude-sonnet-4-6",
                provider="anthropic",
                display_name="Claude Sonnet 4.6",
                tier=ModelTier.ULTRA,
                context_window=200_000,
                max_output_tokens=8_192,
                supports_tools=True,
                strong_at=[TaskType.CODE, TaskType.RESEARCH, TaskType.GENERAL],
                tags=["coding", "large-context", "instruct", "multilingual"],
            ),
            ModelSpec(
                id="claude-opus-4-6",
                provider="anthropic",
                display_name="Claude Opus 4.6",
                tier=ModelTier.ULTRA,
                context_window=200_000,
                max_output_tokens=8_192,
                strong_at=[TaskType.REASONING, TaskType.RESEARCH, TaskType.CREATIVE],
                tags=["reasoning", "large-context", "creative", "multilingual"],
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
        system_parts: list[str] = []
        anthropic_messages: list[dict[str, object]] = []
        for message in messages:
            content_text = message.content if isinstance(message.content, str) else str(message.content)
            if message.role == "system":
                system_parts.append(content_text)
                continue
            role = "assistant" if message.role == "assistant" else "user"
            anthropic_messages.append({"role": role, "content": content_text})

        payload: dict[str, object] = {
            "model": model.id,
            "max_tokens": min(max_tokens, model.max_output_tokens),
            "messages": anthropic_messages,
            "temperature": temperature,
        }
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)

        headers = {
            "x-api-key": self._api_key or "",
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        started = time.monotonic()
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.post(_API_URL, json=payload, headers=headers)

        if resp.status_code == 429:
            raise RuntimeError("quota_exceeded: Anthropic rate limit hit")
        if resp.status_code != 200:
            raise RuntimeError(f"Anthropic API error {resp.status_code}: {resp.text[:200]}")

        data = resp.json()
        latency = (time.monotonic() - started) * 1000
        content_parts = data.get("content", [])
        text = "".join(part.get("text", "") for part in content_parts if part.get("type") == "text")
        usage = data.get("usage", {})

        return ForgeResponse(
            content=text,
            model_id=model.id,
            provider="anthropic",
            latency_ms=latency,
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            finish_reason=data.get("stop_reason", "end_turn"),
        )
