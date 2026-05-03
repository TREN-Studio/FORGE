from __future__ import annotations

import asyncio
import unittest
from pathlib import Path

from forge.core.models import ForgeResponse, Message, ModelSpec, ModelTier, ProviderStatus, TaskType
from forge.core.router import ForgeRouter, classify_query_speed, classify_speed_label, timeout_for_prompt
from forge.providers.base import BaseProvider
from forge.providers.registry import MAX_PROGRESSIVE_ATTEMPTS, progressive_attempt_timeout


class TimeoutProvider(BaseProvider):
    name = "timeout"

    @property
    def models(self) -> list[ModelSpec]:
        return [
            ModelSpec(
                id="timeout-model",
                provider=self.name,
                display_name="Timeout Model",
                tier=ModelTier.ULTRA,
                strong_at=[TaskType.GENERAL],
                tags=["slow"],
            )
        ]

    async def complete(
        self,
        model: ModelSpec,
        messages: list[Message],
        max_tokens: int = 2048,
        temperature: float = 0.7,
    ) -> ForgeResponse:
        await asyncio.sleep(0.1)
        return ForgeResponse(content="late", model_id=model.id, provider=self.name, latency_ms=100)


class ErrorProvider(BaseProvider):
    name = "error"

    @property
    def models(self) -> list[ModelSpec]:
        return [
            ModelSpec(
                id="error-model",
                provider=self.name,
                display_name="Error Model",
                tier=ModelTier.PRO,
                strong_at=[TaskType.GENERAL],
                tags=["error"],
            )
        ]

    async def complete(
        self,
        model: ModelSpec,
        messages: list[Message],
        max_tokens: int = 2048,
        temperature: float = 0.7,
    ) -> ForgeResponse:
        raise RuntimeError("temporary provider error")


class SuccessProvider(BaseProvider):
    name = "success"

    @property
    def models(self) -> list[ModelSpec]:
        return [
            ModelSpec(
                id="success-model",
                provider=self.name,
                display_name="Success Model",
                tier=ModelTier.FAST,
                strong_at=[TaskType.GENERAL],
                tags=["fast"],
            )
        ]

    async def complete(
        self,
        model: ModelSpec,
        messages: list[Message],
        max_tokens: int = 2048,
        temperature: float = 0.7,
    ) -> ForgeResponse:
        return ForgeResponse(content="ok", model_id=model.id, provider=self.name, latency_ms=5)


class SpeedRoutingTests(unittest.IsolatedAsyncioTestCase):
    def test_speed_classification_and_budgets(self) -> None:
        self.assertEqual(classify_speed_label("hi"), "fast")
        self.assertEqual(classify_query_speed("hi"), "fast_queries")
        self.assertEqual(timeout_for_prompt("hi"), 8.0)

        self.assertEqual(classify_speed_label("analyze this URL and summarize the important points"), "normal")
        self.assertEqual(timeout_for_prompt("analyze this URL and summarize the important points"), 15.0)

        complex_prompt = "Create a small Python project with src main tests then run the tests and save a report"
        self.assertEqual(classify_speed_label(complex_prompt), "complex")
        self.assertEqual(timeout_for_prompt(complex_prompt), 30.0)

    def test_progressive_timeouts_stay_inside_budget(self) -> None:
        total = sum(progressive_attempt_timeout(8.0, index) for index in range(MAX_PROGRESSIVE_ATTEMPTS))
        self.assertLessEqual(total, 8.01)
        self.assertGreater(progressive_attempt_timeout(8.0, 0), progressive_attempt_timeout(8.0, 1))
        self.assertGreater(progressive_attempt_timeout(8.0, 1), progressive_attempt_timeout(8.0, 2))

    def test_desktop_stream_has_progressive_wait_statuses(self) -> None:
        runtime_source = Path("forge/desktop/runtime.py").read_text(encoding="utf-8")
        self.assertIn("Finding best model...", runtime_source)
        self.assertIn("Switching to faster provider...", runtime_source)
        self.assertIn("Almost ready...", runtime_source)

    async def test_router_uses_three_progressive_attempts_and_demotes_slow_provider(self) -> None:
        router = ForgeRouter()
        router.register(TimeoutProvider())
        router.register(ErrorProvider())
        router.register(SuccessProvider())
        router._scores["timeout::timeout-model"].quality_score = 1.0
        router._scores["error::error-model"].quality_score = 0.9
        router._scores["success::success-model"].quality_score = 0.1

        response = await router.route(
            [Message(role="user", content="please summarize this short note and answer clearly")],
            task_type=TaskType.GENERAL,
            timeout=0.03,
        )

        telemetry = response.routing_telemetry
        self.assertEqual(response.provider, "success")
        self.assertEqual(len(telemetry["attempts"]), 3)
        self.assertEqual(telemetry["fallback_count"], 2)
        self.assertEqual(telemetry["query_speed"], "normal")
        self.assertEqual(router._scores["timeout::timeout-model"].status, ProviderStatus.SLOW)
        self.assertTrue(all("attempt_timeout_s" in attempt for attempt in telemetry["attempts"]))


if __name__ == "__main__":
    unittest.main()
