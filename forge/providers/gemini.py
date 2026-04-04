"""
FORGE × Google Gemini Provider
================================
1 million free tokens per day.
128K context window. Multimodal. Fast.

Free tier: 1M tokens/day · 1,500 requests/day
Sign up  : https://aistudio.google.com (free API key in seconds)
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

_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


class GeminiProvider(BaseProvider):
    """Google Gemini via the free Generative Language API."""

    name: ClassVar[str] = "gemini"
    daily_token_limit:   int = 1_000_000
    daily_request_limit: int = 1_500

    @property
    def models(self) -> list[ModelSpec]:
        return [
            ModelSpec(
                id="gemini-2.5-flash",
                provider="gemini",
                display_name="Gemini 2.5 Flash",
                tier=ModelTier.ULTRA,
                context_window=1_048_576,
                max_output_tokens=8_192,
                supports_vision=True,
                supports_tools=True,
                strong_at=[TaskType.RESEARCH, TaskType.CREATIVE, TaskType.GENERAL],
                tags=["large-context", "vision", "instruct", "fast"],
            ),
            ModelSpec(
                id="gemini-2.0-flash",
                provider="gemini",
                display_name="Gemini 2.0 Flash",
                tier=ModelTier.PRO,
                context_window=1_048_576,
                max_output_tokens=8_192,
                supports_vision=True,
                supports_tools=True,
                strong_at=[TaskType.RESEARCH, TaskType.GENERAL],
                tags=["large-context", "vision", "instruct"],
            ),
            ModelSpec(
                id="gemini-2.0-flash-lite",
                provider="gemini",
                display_name="Gemini 2.0 Flash Lite",
                tier=ModelTier.FAST,
                context_window=1_048_576,
                max_output_tokens=8_192,
                supports_vision=True,
                strong_at=[TaskType.FAST, TaskType.GENERAL],
                tags=["fast", "large-context"],
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
        # Convert OpenAI-style messages to Gemini format
        contents = []
        system_prompt = ""
        for msg in messages:
            if msg.role == "system":
                system_prompt = msg.content if isinstance(msg.content, str) else str(msg.content)
                continue
            role = "model" if msg.role == "assistant" else "user"
            content_text = msg.content if isinstance(msg.content, str) else str(msg.content)
            contents.append({"role": role, "parts": [{"text": content_text}]})

        payload: dict = {
            "contents": contents,
            "generationConfig": {
                "maxOutputTokens": max_tokens,
                "temperature":     temperature,
            },
        }
        if system_prompt:
            payload["systemInstruction"] = {"parts": [{"text": system_prompt}]}

        url = f"{_API_BASE}/{model.id}:generateContent?key={self._api_key}"

        t0 = time.monotonic()
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(url, json=payload)

        if resp.status_code == 429:
            raise RuntimeError("quota_exceeded: Gemini rate limit hit")
        if resp.status_code != 200:
            raise RuntimeError(f"Gemini API error {resp.status_code}: {resp.text[:200]}")

        data    = resp.json()
        latency = (time.monotonic() - t0) * 1000

        candidate = data["candidates"][0]
        text = candidate["content"]["parts"][0].get("text", "")
        usage = data.get("usageMetadata", {})

        return ForgeResponse(
            content       = text,
            model_id      = model.id,
            provider      = "gemini",
            latency_ms    = latency,
            input_tokens  = usage.get("promptTokenCount", 0),
            output_tokens = usage.get("candidatesTokenCount", 0),
            finish_reason = candidate.get("finishReason", "STOP").lower(),
        )
