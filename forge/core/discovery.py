"""
FORGE Self-Discovery Engine
============================
Discovers new free models and attaches compatible ones to the live router.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

import httpx

from forge.core.models import ModelSpec, ModelTier, TaskType

logger = logging.getLogger("forge.discovery")


@dataclass
class DiscoveredModel:
    provider: str
    model_id: str
    display_name: str
    context_window: int = 8_192
    is_free: bool = True
    tags: list[str] = field(default_factory=list)
    discovered_at: float = field(default_factory=time.time)


class SelfDiscoveryEngine:
    """
    Discovers new free models across providers every 24 hours.
    If a router is attached, compatible models are registered immediately.
    """

    INTERVAL_HOURS = 24

    def __init__(self, router=None) -> None:
        self._router = router
        self._running = False
        self._task: asyncio.Task | None = None
        self._last_run: float = 0.0
        self._discovered: list[DiscoveredModel] = []

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("Self-Discovery Engine started")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()

    async def run_once(self) -> dict:
        """Run a full discovery scan right now and attach new models when possible."""
        results: list[DiscoveredModel] = []

        tasks = [
            self._discover_openrouter(),
            self._discover_groq_models(),
            self._discover_ollama_library(),
            self._discover_huggingface(),
        ]
        gathered = await asyncio.gather(*tasks, return_exceptions=True)
        for item in gathered:
            if isinstance(item, list):
                results.extend(item)

        self._discovered = results
        self._last_run = time.time()

        attached_by_provider = self._attach_to_router(results)
        attached_total = sum(attached_by_provider.values())

        logger.info(
            f"Discovery complete â€” found {len(results)} free models, attached {attached_total}"
        )
        return {
            "discovered": len(results),
            "attached": attached_total,
            "providers": attached_by_provider,
            "models": [f"{model.provider}/{model.model_id}" for model in results],
            "last_run": self._last_run,
        }

    async def _discover_openrouter(self) -> list[DiscoveredModel]:
        url = "https://openrouter.ai/api/v1/models"
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    url,
                    headers={"HTTP-Referer": "https://www.trenstudio.com/FORGE"},
                )
            if resp.status_code != 200:
                return []

            data = resp.json().get("data", [])
            models: list[DiscoveredModel] = []
            for item in data:
                pricing = item.get("pricing", {})
                is_free = item["id"].endswith(":free")
                if not is_free:
                    try:
                        is_free = (
                            float(pricing.get("prompt", "1")) == 0.0
                            and float(pricing.get("completion", "1")) == 0.0
                        )
                    except (TypeError, ValueError):
                        is_free = False
                if not is_free:
                    continue

                models.append(
                    DiscoveredModel(
                        provider="openrouter",
                        model_id=item["id"],
                        display_name=item.get("name", item["id"]),
                        context_window=item.get("context_length", 8_192),
                        is_free=True,
                        tags=["free", "openrouter"],
                    )
                )

            logger.info(f"OpenRouter: {len(models)} free models found")
            return models
        except Exception as exc:
            logger.debug(f"OpenRouter discovery failed: {exc}")
            return []

    async def _discover_groq_models(self) -> list[DiscoveredModel]:
        import os

        key = os.environ.get("FORGE_GROQ_KEY") or self._read_key("groq")
        if not key:
            return []

        url = "https://api.groq.com/openai/v1/models"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url, headers={"Authorization": f"Bearer {key}"})
            if resp.status_code != 200:
                return []

            data = resp.json().get("data", [])
            models = [
                DiscoveredModel(
                    provider="groq",
                    model_id=item["id"],
                    display_name=item.get("id", item["id"]),
                    is_free=True,
                    tags=["groq", "fast"],
                )
                for item in data
            ]
            logger.info(f"Groq: {len(models)} models found")
            return models
        except Exception as exc:
            logger.debug(f"Groq model discovery failed: {exc}")
            return []

    async def _discover_ollama_library(self) -> list[DiscoveredModel]:
        known_popular = [
            ("llama3.3", "LLaMA 3.3 70B", 131072),
            ("llama3.1:8b", "LLaMA 3.1 8B", 131072),
            ("deepseek-r1", "DeepSeek R1", 32768),
            ("deepseek-r1:7b", "DeepSeek R1 7B", 32768),
            ("qwen2.5:72b", "Qwen 2.5 72B", 32768),
            ("qwen2.5-coder:32b", "Qwen 2.5 Coder 32B", 32768),
            ("mistral", "Mistral 7B", 32768),
            ("codellama:34b", "CodeLLaMA 34B", 16384),
            ("phi4", "Phi-4 14B", 16384),
            ("gemma2:27b", "Gemma 2 27B", 8192),
        ]
        return [
            DiscoveredModel(
                provider="ollama",
                model_id=model_id,
                display_name=f"{name} (local)",
                context_window=context,
                is_free=True,
                tags=["local", "private", "free"],
            )
            for model_id, name, context in known_popular
        ]

    async def _discover_huggingface(self) -> list[DiscoveredModel]:
        curated = [
            ("meta-llama/Meta-Llama-3.1-70B-Instruct", "LLaMA 3.1 70B (HF)", 128000),
            ("meta-llama/Meta-Llama-3.1-8B-Instruct", "LLaMA 3.1 8B (HF)", 128000),
            ("mistralai/Mistral-7B-Instruct-v0.3", "Mistral 7B (HF)", 32768),
            ("Qwen/Qwen2.5-72B-Instruct", "Qwen 2.5 72B (HF)", 32768),
            ("deepseek-ai/DeepSeek-R1-Distill-Qwen-32B", "DeepSeek R1 32B (HF)", 32768),
            ("microsoft/Phi-3.5-mini-instruct", "Phi-3.5 Mini (HF)", 128000),
        ]
        return [
            DiscoveredModel(
                provider="huggingface",
                model_id=model_id,
                display_name=name,
                context_window=context,
                is_free=True,
                tags=["huggingface", "free"],
            )
            for model_id, name, context in curated
        ]

    async def _loop(self) -> None:
        while self._running:
            try:
                await self.run_once()
            except Exception as exc:
                logger.error(f"Discovery loop error: {exc}")
            await asyncio.sleep(self.INTERVAL_HOURS * 3600)

    def _attach_to_router(self, models: list[DiscoveredModel]) -> dict[str, int]:
        if self._router is None:
            return {}

        grouped: dict[str, list[ModelSpec]] = {}
        for model in models:
            provider = self._router.get_provider(model.provider)
            if provider is None:
                continue
            grouped.setdefault(model.provider, []).append(self._to_model_spec(model))

        attached: dict[str, int] = {}
        for provider_name, specs in grouped.items():
            added = self._router.register_models(provider_name, specs)
            if added:
                attached[provider_name] = added
        return attached

    def _to_model_spec(self, model: DiscoveredModel) -> ModelSpec:
        text = f"{model.model_id} {model.display_name}".lower()
        tags = list(dict.fromkeys(model.tags))
        strong_at = [TaskType.GENERAL]
        tier = self._infer_tier(text)
        supports_vision = any(token in text for token in ("vision", "vl", "multimodal"))

        if any(token in text for token in ("coder", "code", "codestral", "devstral")):
            strong_at.append(TaskType.CODE)
            tags.append("coding")
        if any(token in text for token in ("reason", "r1", "think", "math")):
            strong_at.extend([TaskType.REASONING, TaskType.MATH])
            tags.extend(["reasoning", "r1"])
        if any(token in text for token in ("creative", "story", "write")):
            strong_at.append(TaskType.CREATIVE)
        if model.context_window >= 64_000:
            strong_at.append(TaskType.RESEARCH)
            tags.append("large-context")
        if any(token in text for token in ("flash", "instant", "turbo")):
            strong_at.append(TaskType.FAST)
            tags.append("fast")
        if supports_vision:
            tags.append("vision")

        return ModelSpec(
            id=model.model_id,
            provider=model.provider,
            display_name=model.display_name,
            tier=tier,
            context_window=model.context_window,
            max_output_tokens=min(16_384, model.context_window),
            free=model.is_free,
            supports_vision=supports_vision,
            supports_tools=model.provider != "ollama",
            strong_at=list(dict.fromkeys(strong_at)),
            tags=list(dict.fromkeys(tags)),
        )

    @staticmethod
    def _infer_tier(text: str) -> ModelTier:
        if any(token in text for token in ("405b", "671b", "72b", "70b", "large")):
            return ModelTier.ULTRA
        if any(token in text for token in ("34b", "32b", "27b", "22b", "14b", "13b")):
            return ModelTier.PRO
        if any(token in text for token in ("8b", "7b")):
            return ModelTier.BASE
        return ModelTier.FAST

    @staticmethod
    def _read_key(provider: str) -> str | None:
        from pathlib import Path

        keyfile = Path.home() / ".forge" / "keys" / provider
        if keyfile.exists():
            return keyfile.read_text().strip()
        return None
