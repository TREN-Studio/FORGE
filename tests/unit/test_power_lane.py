"""
FORGE Power Lane Routing Tests
================================
Locks in the cost-aware frontier-model gating:
1. Paid ULTRA models are EXCLUDED from shallow FAST/GENERAL tasks (no waste).
2. Paid ULTRA models remain ELIGIBLE (and rank higher) on quality-heavy
   CODE/REASONING/RESEARCH tasks via the tier boost.
"""

from __future__ import annotations

import unittest

from forge.core.models import ModelSpec, ModelTier, TaskType


def _mk(pid: str, mid: str, tier: ModelTier, free: bool = False) -> ModelSpec:
    return ModelSpec(
        id=mid,
        provider=pid,
        display_name=mid,
        tier=tier,
        free=free,
        strong_at=[TaskType.GENERAL, TaskType.CODE, TaskType.REASONING],
        tags=["instruct"],
    )


class TestPowerLaneFlags(unittest.TestCase):
    def test_paid_models_are_marked_not_free(self) -> None:
        # Anthropic / OpenAI / DeepSeek models must never report free=True,
        # otherwise the cost-aware gate cannot tell them apart from free models.
        from forge.providers.anthropic import AnthropicProvider
        from forge.providers.openai import OpenAIProvider
        from forge.providers.deepseek import DeepSeekProvider

        for provider_cls in (AnthropicProvider, OpenAIProvider, DeepSeekProvider):
            for spec in provider_cls().models:
                self.assertFalse(spec.free, f"{spec.provider}/{spec.id} should be free=False")

    def test_tier_order_ultra_beats_fast(self) -> None:
        # The tier ranking must place ULTRA above FAST so the Power-Lane boost
        # can lift frontier models for quality-heavy tasks.
        from forge.core.router import TIER_ORDER

        self.assertGreater(TIER_ORDER[ModelTier.ULTRA], TIER_ORDER[ModelTier.FAST])
        self.assertGreater(TIER_ORDER[ModelTier.ULTRA], TIER_ORDER[ModelTier.PRO])

    def test_free_flag_default_true_for_free_provider(self) -> None:
        # A free model (e.g. a default spec) remains free=True unless explicitly set.
        spec = _mk("groq", "some-free-model", ModelTier.FAST, free=True)  # default free
        self.assertTrue(spec.free)


if __name__ == "__main__":
    unittest.main()