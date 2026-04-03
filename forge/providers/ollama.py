"""
FORGE × Ollama Provider
=========================
100% local. 100% private. 100% free. No API key. No internet.
The ultimate fallback — FORGE works even offline.

Install: https://ollama.com
Models : ollama pull llama3.3 / ollama pull deepseek-r1

Ollama is FORGE's zero-dependency baseline. If every cloud provider
is down or quota-exhausted, Ollama keeps FORGE running forever.
"""

from __future__ import annotations

import asyncio
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

_API_URL = "http://localhost:11434/api/chat"
_TAGS_URL = "http://localhost:11434/api/tags"


class OllamaProvider(BaseProvider):
    """
    Ollama local inference — zero cost, zero privacy concerns.
    Auto-discovers all locally installed models.
    """

    name: ClassVar[str] = "ollama"
    daily_token_limit:   int = 0   # unlimited
    daily_request_limit: int = 0   # unlimited

    def __init__(self, api_key: str | None = None) -> None:
        self._discovered_models: list[ModelSpec] = []
        self._discovery_done = False
        super().__init__(api_key)

    @property
    def models(self) -> list[ModelSpec]:
        """Return discovered local models plus known defaults."""
        if self._discovered_models:
            return self._discovered_models
        # Sync fallback: return known popular models
        return self._known_models()

    @property
    def is_available(self) -> bool:
        """Ollama requires no key — just needs to be running."""
        return True  # we check connectivity lazily

    def _load_key(self) -> str | None:
        return None   # Ollama is keyless

    # ── Discovery ────────────────────────────────────────────────

    async def discover_local_models(self) -> list[ModelSpec]:
        """Query Ollama for installed models and build ModelSpec list."""
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                resp = await client.get(_TAGS_URL)
            if resp.status_code != 200:
                return self._known_models()
            data = resp.json()
            specs = []
            for m in data.get("models", []):
                model_name = m["name"]
                size_gb    = m.get("size", 0) / 1e9
                specs.append(self._spec_from_name(model_name, size_gb))
            self._discovered_models = specs
            self._discovery_done = True
            return specs
        except (httpx.ConnectError, httpx.TimeoutException):
            return self._known_models()

    def _spec_from_name(self, name: str, size_gb: float) -> ModelSpec:
        """Infer spec from model name and size."""
        name_lower = name.lower()
        tier = ModelTier.BASE
        if size_gb > 35:
            tier = ModelTier.ULTRA
        elif size_gb > 10:
            tier = ModelTier.PRO

        strong_at: list[TaskType] = [TaskType.GENERAL]
        tags: list[str] = ["local", "private"]

        if any(k in name_lower for k in ("code", "coder", "wizard")):
            strong_at.append(TaskType.CODE)
            tags.append("coding")
        if any(k in name_lower for k in ("math", "reason", "r1")):
            strong_at.append(TaskType.MATH)
            strong_at.append(TaskType.REASONING)
            tags.append("reasoning")
        if "instruct" in name_lower or "chat" in name_lower:
            tags.append("instruct")

        return ModelSpec(
            id=name,
            provider="ollama",
            display_name=f"{name} (local)",
            tier=tier,
            context_window=8_192,
            max_output_tokens=4_096,
            supports_tools=False,
            strong_at=strong_at,
            tags=tags,
        )

    def _known_models(self) -> list[ModelSpec]:
        """Well-known Ollama models — shown even if not yet pulled."""
        return [
            ModelSpec(
                id="llama3.3",
                provider="ollama",
                display_name="LLaMA 3.3 (local)",
                tier=ModelTier.ULTRA,
                context_window=131_072,
                max_output_tokens=8_192,
                strong_at=[TaskType.GENERAL, TaskType.CODE, TaskType.REASONING],
                tags=["local", "private", "instruct"],
            ),
            ModelSpec(
                id="deepseek-r1",
                provider="ollama",
                display_name="DeepSeek R1 (local)",
                tier=ModelTier.PRO,
                context_window=32_768,
                max_output_tokens=8_192,
                strong_at=[TaskType.REASONING, TaskType.MATH, TaskType.CODE],
                tags=["local", "private", "reasoning", "r1"],
            ),
            ModelSpec(
                id="mistral",
                provider="ollama",
                display_name="Mistral 7B (local)",
                tier=ModelTier.BASE,
                context_window=32_768,
                max_output_tokens=8_192,
                strong_at=[TaskType.GENERAL, TaskType.CODE],
                tags=["local", "private", "instruct"],
            ),
        ]

    # ── Completion ────────────────────────────────────────────────

    async def complete(
        self,
        model:       ModelSpec,
        messages:    list[Message],
        max_tokens:  int   = 2048,
        temperature: float = 0.7,
    ) -> ForgeResponse:
        payload = {
            "model":    model.id,
            "messages": [m.model_dump(exclude_none=True) for m in messages],
            "stream":   False,
            "options":  {
                "num_predict": max_tokens,
                "temperature": temperature,
            },
        }

        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(_API_URL, json=payload)
        except httpx.ConnectError:
            raise RuntimeError(
                "Ollama not running. Start it with: ollama serve\n"
                "Install: https://ollama.com"
            )

        if resp.status_code != 200:
            raise RuntimeError(f"Ollama error {resp.status_code}: {resp.text[:200]}")

        data    = resp.json()
        latency = (time.monotonic() - t0) * 1000

        return ForgeResponse(
            content       = data["message"]["content"],
            model_id      = model.id,
            provider      = "ollama",
            latency_ms    = latency,
            input_tokens  = data.get("prompt_eval_count", 0),
            output_tokens = data.get("eval_count", 0),
            finish_reason = "stop" if data.get("done") else "length",
        )
