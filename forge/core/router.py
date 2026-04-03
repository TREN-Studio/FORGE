"""
FORGE Smart Selector Engine
============================
This is what makes FORGE different from everything else.

Every call goes through here. The router scores every available model
in real-time — latency, quota, quality, task fit — and picks the
absolute best option. When that fails, it falls back instantly.

No configuration needed. No API key required to start.
It just works, and it gets smarter with every call.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from forge.core.models import (
    ForgeResponse,
    Message,
    ModelScore,
    ModelSpec,
    ModelTier,
    ProviderStatus,
    TaskType,
)

if TYPE_CHECKING:
    from forge.providers.base import BaseProvider

logger = logging.getLogger("forge.router")


# ─────────────────────────────────────────────
#  Task → Model affinity map
#  Which model tiers and tags excel at which tasks?
# ─────────────────────────────────────────────

TASK_AFFINITY: dict[TaskType, dict] = {
    TaskType.CODE: {
        "preferred_providers": ["groq", "deepseek", "mistral", "together"],
        "preferred_tags":      ["coding", "instruct"],
        "min_tier":            ModelTier.PRO,
        "latency_weight":      0.15,   # code quality > speed
        "quality_weight":      0.55,
    },
    TaskType.MATH: {
        "preferred_providers": ["deepseek", "groq", "gemini"],
        "preferred_tags":      ["math", "reasoning", "r1"],
        "min_tier":            ModelTier.PRO,
        "latency_weight":      0.10,
        "quality_weight":      0.60,
    },
    TaskType.RESEARCH: {
        "preferred_providers": ["gemini", "openrouter", "together"],
        "preferred_tags":      ["large-context", "instruct"],
        "min_tier":            ModelTier.PRO,
        "latency_weight":      0.10,
        "quality_weight":      0.55,
    },
    TaskType.CREATIVE: {
        "preferred_providers": ["gemini", "mistral", "together"],
        "preferred_tags":      ["instruct", "creative"],
        "min_tier":            ModelTier.BASE,
        "latency_weight":      0.20,
        "quality_weight":      0.45,
    },
    TaskType.FAST: {
        "preferred_providers": ["groq", "together", "deepseek"],
        "preferred_tags":      ["fast", "instruct"],
        "min_tier":            ModelTier.FAST,
        "latency_weight":      0.55,   # speed is everything here
        "quality_weight":      0.20,
    },
    TaskType.REASONING: {
        "preferred_providers": ["deepseek", "groq", "gemini"],
        "preferred_tags":      ["reasoning", "r1", "think"],
        "min_tier":            ModelTier.PRO,
        "latency_weight":      0.05,
        "quality_weight":      0.65,
    },
    TaskType.GENERAL: {
        "preferred_providers": [],     # no preference — pure score
        "preferred_tags":      [],
        "min_tier":            ModelTier.FAST,
        "latency_weight":      0.20,
        "quality_weight":      0.45,
    },
}

TIER_ORDER = {
    ModelTier.ULTRA: 4,
    ModelTier.PRO:   3,
    ModelTier.BASE:  2,
    ModelTier.FAST:  1,
}


class ForgeRouter:
    """
    The FORGE Smart Selector Engine.

    Usage:
        router = ForgeRouter()
        router.register(GroqProvider())
        router.register(GeminiProvider())
        response = await router.route(messages, task_type=TaskType.CODE)
    """

    def __init__(self) -> None:
        self._providers: dict[str, BaseProvider] = {}
        self._scores:    dict[str, ModelScore]   = {}   # key = "provider::model_id"
        self._lock = asyncio.Lock()

    # ── Registration ──────────────────────────────────────────────

    def register(self, provider: BaseProvider) -> None:
        """Register a provider and all its models."""
        self._providers[provider.name] = provider
        for spec in provider.list_models():
            key = self._key(provider.name, spec.id)
            if key not in self._scores:
                self._scores[key] = ModelScore(
                    model_id=spec.id,
                    provider=provider.name,
                    quality_score=self._initial_quality(spec),
                    tokens_limit_daily=provider.daily_token_limit,
                    requests_limit_daily=provider.daily_request_limit,
                )
        logger.info(f"Registered provider '{provider.name}' with {len(provider.list_models())} models")

    def register_models(self, provider_name: str, models: list[ModelSpec]) -> int:
        """Attach newly discovered models to an existing provider."""
        provider = self._providers.get(provider_name)
        if provider is None:
            return 0

        added = provider.add_models(models)
        if added == 0:
            return 0

        self.register(provider)
        return added

    def get_provider(self, provider_name: str) -> BaseProvider | None:
        return self._providers.get(provider_name)

    async def mark_provider_quota(
        self,
        provider_name: str,
        reset_at: float = 0.0,
    ) -> None:
        """Temporarily remove an exhausted provider from ranking."""
        async with self._lock:
            for score in self._scores.values():
                if score.provider != provider_name:
                    continue
                score.status = ProviderStatus.QUOTA
                score.quota_reset_at = reset_at

    async def reset_provider_quotas(self, provider_name: str | None = None) -> None:
        """Reset score-side quota state for one provider or the whole fleet."""
        async with self._lock:
            for score in self._scores.values():
                if provider_name and score.provider != provider_name:
                    continue
                score.tokens_used_today = 0
                score.requests_used_today = 0
                score.quota_reset_at = 0.0
                if score.status == ProviderStatus.QUOTA:
                    score.status = ProviderStatus.ONLINE

        if provider_name:
            logger.info(f"Quota state reset for provider '{provider_name}'")
        else:
            logger.info("Daily quotas reset for all providers")

    # ── Routing ───────────────────────────────────────────────────

    async def route(
        self,
        messages:  list[Message],
        task_type: TaskType = TaskType.GENERAL,
        max_tokens: int     = 2048,
        temperature: float  = 0.7,
        require_vision: bool = False,
        timeout: float      = 45.0,
    ) -> ForgeResponse:
        """
        Pick the best available model and call it.
        Falls back through the ranked list until one succeeds.
        """
        ranked = self._rank(task_type, require_vision)
        if not ranked:
            raise RuntimeError("No models available. Run `forge add-key` to add a provider.")

        last_error: Exception | None = None
        for key, score in ranked:
            provider = self._providers[score.provider]
            spec     = provider.get_model(score.model_id)
            if spec is None:
                continue

            logger.debug(f"Trying {score.provider}/{score.model_id} (score={score.composite_score:.3f})")
            t_start = time.monotonic()
            try:
                raw = await asyncio.wait_for(
                    provider.complete(
                        model=spec,
                        messages=messages,
                        max_tokens=min(max_tokens, spec.max_output_tokens),
                        temperature=temperature,
                    ),
                    timeout=timeout,
                )
                latency = (time.monotonic() - t_start) * 1000
                async with self._lock:
                    score.record_success(latency, raw.input_tokens + raw.output_tokens)

                raw.score_used = score.composite_score
                logger.info(
                    f"✓ {score.provider}/{score.model_id} "
                    f"[{latency:.0f}ms · {raw.total_tokens} tok]"
                )
                return raw

            except asyncio.TimeoutError:
                async with self._lock:
                    score.record_failure("timeout")
                    score.status = ProviderStatus.SLOW
                last_error = TimeoutError(f"{score.provider}/{score.model_id} timed out")
                logger.warning(f"✗ Timeout on {score.provider}/{score.model_id}")

            except Exception as exc:
                async with self._lock:
                    score.record_failure(str(exc))
                    if "quota" in str(exc).lower() or "rate" in str(exc).lower():
                        score.status = ProviderStatus.QUOTA
                last_error = exc
                logger.warning(f"✗ Error on {score.provider}/{score.model_id}: {exc}")

        raise RuntimeError(
            f"All models failed for task '{task_type}'. Last error: {last_error}"
        )

    # ── Ranking ───────────────────────────────────────────────────

    def _rank(
        self,
        task_type: TaskType,
        require_vision: bool = False,
    ) -> list[tuple[str, ModelScore]]:
        """
        Produce a ranked list of (key, ModelScore) for a given task.
        This is the selection algorithm — the core of FORGE's intelligence.
        """
        affinity = TASK_AFFINITY.get(task_type, TASK_AFFINITY[TaskType.GENERAL])
        min_tier_rank = TIER_ORDER.get(affinity["min_tier"], 0)

        candidates: list[tuple[str, ModelScore, float]] = []

        for key, score in self._scores.items():
            if score.status == ProviderStatus.OFFLINE:
                continue
            if score.status == ProviderStatus.QUOTA:
                # check if quota has reset
                if time.time() < score.quota_reset_at:
                    continue
                else:
                    score.status = ProviderStatus.ONLINE

            provider = self._providers.get(score.provider)
            if provider is None or not provider.is_available:
                continue

            spec = provider.get_model(score.model_id)
            if spec is None:
                continue
            if require_vision and not spec.supports_vision:
                continue
            if TIER_ORDER.get(spec.tier, 0) < min_tier_rank:
                continue

            # Compute task-adjusted score
            adjusted = self._adjusted_score(score, spec, affinity)
            candidates.append((key, score, adjusted))

        candidates.sort(key=lambda x: x[2], reverse=True)
        return [(k, s) for k, s, _ in candidates]

    def _adjusted_score(
        self,
        score:    ModelScore,
        spec:     ModelSpec,
        affinity: dict,
    ) -> float:
        """Adjust composite score using task affinity bonuses."""
        base = score.composite_score
        if base == 0.0:
            return 0.0

        bonus = 0.0

        # Provider preference bonus
        if score.provider in affinity.get("preferred_providers", []):
            bonus += 0.08

        # Tag bonus
        for tag in affinity.get("preferred_tags", []):
            if tag in spec.tags:
                bonus += 0.05
                break

        # Tier bonus for quality-heavy tasks
        tier_rank = TIER_ORDER.get(spec.tier, 0)
        quality_w = affinity.get("quality_weight", 0.45)
        bonus += tier_rank * 0.015 * quality_w

        # Latency adjustment for speed-critical tasks
        latency_w = affinity.get("latency_weight", 0.20)
        speed = min(1.0, 500.0 / max(score.latency_ms, 50))
        bonus += speed * latency_w * 0.1

        return min(1.0, base + bonus)

    # ── Introspection ─────────────────────────────────────────────

    def leaderboard(self, task_type: TaskType = TaskType.GENERAL) -> list[dict]:
        """Return the current model rankings as a list of dicts."""
        ranked = self._rank(task_type)
        result = []
        for key, score in ranked:
            provider = self._providers.get(score.provider)
            spec = provider.get_model(score.model_id) if provider else None
            result.append({
                "rank":         len(result) + 1,
                "model":        f"{score.provider}/{score.model_id}",
                "score":        round(score.composite_score, 4),
                "latency_ms":   round(score.latency_ms, 0),
                "success_rate": round(score.success_rate, 3),
                "quota_left":   f"{score.quota_fraction * 100:.0f}%",
                "tier":         spec.tier.value if spec else "?",
                "status":       score.status.value,
            })
        return result

    def status(self) -> dict:
        """Full system status snapshot."""
        total    = len(self._scores)
        online   = sum(1 for s in self._scores.values() if s.status == ProviderStatus.ONLINE)
        on_quota = sum(1 for s in self._scores.values() if s.status == ProviderStatus.QUOTA)
        return {
            "providers":   len(self._providers),
            "models_total":   total,
            "models_online":  online,
            "models_quota":   on_quota,
            "models_offline": total - online - on_quota,
        }

    # ── Quota Reset ───────────────────────────────────────────────

    async def reset_daily_quotas(self) -> None:
        """Called by the Quota Guardian at midnight UTC."""
        await self.reset_provider_quotas()

    # ── Helpers ───────────────────────────────────────────────────

    @staticmethod
    def _key(provider: str, model_id: str) -> str:
        return f"{provider}::{model_id}"

    @staticmethod
    def _initial_quality(spec: ModelSpec) -> float:
        """Assign a sensible initial quality score before we have real data."""
        base = {
            ModelTier.ULTRA: 0.92,
            ModelTier.PRO:   0.80,
            ModelTier.BASE:  0.68,
            ModelTier.FAST:  0.55,
        }.get(spec.tier, 0.65)
        # Small random jitter so models aren't perfectly tied on first call
        import random
        return round(base + random.uniform(-0.03, 0.03), 4)
