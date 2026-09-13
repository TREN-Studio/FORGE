"""
FORGE Intelligence-Layer Unit Tests
====================================
Locks in the three orchestration upgrades:
1. Semantic memory retrieval with recency decay (ContextMemory).
2. Honest, ratio-derived critic confidence (CriticAgent.review_mission).
3. Deliberative planning guard (PlanningEngine.deliberate returns None
   without a session, so legacy fallback is preserved).

Plus live-debugging lessons (2026-09-12 session) locked as regressions:
4. Internal engine prompts must NOT be short-circuited by the identity
   instant-response guard (allow_instant=False path).
5. Legitimate raw-user identity questions MUST still be instant-answered.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from forge.brain.contracts import (
    CompletionState,
    ExecutionPlan,
    PlanStep,
    RiskLevel,
    StepExecutionResult,
)
from forge.brain.council import CriticAgent
from forge.brain.planner import PlanningEngine
from forge.core.identity import instant_response
from forge.memory.context import ContextMemory
from forge.memory.graph import MemoryGraph


class TestContextMemorySemantic(unittest.TestCase):
    def setUp(self) -> None:
        self.fd, self.db_path = tempfile.mkstemp()
        self.graph = MemoryGraph(db_path=Path(self.db_path))
        self.mem = ContextMemory(self.graph)

    def tearDown(self) -> None:
        self.graph.close()

    def test_retrieves_most_relevant_memory(self) -> None:
        self.graph.remember("project:foodjot", "FoodJot blog uses WordPress and has 600+ articles")
        self.graph.remember("project:forge", "FORGE is a Python agent with a model router")
        self.graph.remember("user", "prefers concise code")
        results = self.mem.retrieve_relevant("wordpress blog articles", limit=2)
        self.assertTrue(any("FoodJot" in r for r in results), results)

    def test_empty_query_returns_empty(self) -> None:
        self.graph.remember("user", "some fact")
        self.assertEqual(self.mem.retrieve_relevant("...", limit=5), [])


class TestCriticHonestConfidence(unittest.TestCase):
    def _step(self, step_id: str, status: CompletionState) -> StepExecutionResult:
        return StepExecutionResult(
            step_id=step_id,
            skill="shell-executor",
            status=status,
            output="ok",
            validation_status=status,
        )

    def _plan(self, n: int) -> ExecutionPlan:
        steps = [
            PlanStep(
                id=f"step_{i}",
                action="x",
                skill="shell-executor",
                expected_output="y",
                validation="z",
            )
            for i in range(1, n + 1)
        ]
        return ExecutionPlan(
            objective="t", task_type="general", risk_level=RiskLevel.LOW, steps=steps
        )

    def test_all_finished_confidence_is_one(self) -> None:
        critic = CriticAgent()
        plan = self._plan(3)
        results = [self._step("step_1", CompletionState.FINISHED) for _ in range(3)]
        review = critic.review_mission(plan, results)
        self.assertEqual(review.confidence, 1.0)

    def test_half_partial_lowers_confidence(self) -> None:
        critic = CriticAgent()
        plan = self._plan(2)
        results = [
            self._step("step_1", CompletionState.FINISHED),
            self._step("step_2", CompletionState.PARTIALLY_FINISHED),
        ]
        review = critic.review_mission(plan, results)
        # (1 + 0.5) / 2 = 0.75
        self.assertEqual(review.confidence, 0.75)

    def test_failed_step_is_not_full_confidence(self) -> None:
        critic = CriticAgent()
        plan = self._plan(2)
        results = [
            self._step("step_1", CompletionState.FINISHED),
            self._step("step_2", CompletionState.FAILED),
        ]
        review = critic.review_mission(plan, results)
        self.assertLess(review.confidence, 1.0)
        self.assertGreaterEqual(review.confidence, 0.0)


class TestDeliberativePlanningGuard(unittest.TestCase):
    def test_deliberate_returns_none_without_session(self) -> None:
        engine = PlanningEngine()
        result = engine.deliberate("some request", None, None, session=None)
        self.assertIsNone(result)


class TestInstantResponseGuardBoundaries(unittest.TestCase):
    """Live-session regression: raw-user identity questions stay instant;
    internal composed prompts are exempt via allow_instant=False."""

    def test_raw_user_identity_questions_still_instant(self) -> None:
        for question in ("What are you?", "who made you?", "hello"):
            self.assertIsNotNone(
                instant_response(question),
                f"Raw user text {question!r} must keep its instant answer",
            )

    def test_internal_composed_prompt_would_have_been_hijacked(self) -> None:
        # This documents WHY allow_instant exists: the composed deliberative
        # prompt accidentally satisfies the {"what","are","you"} subset check.
        from forge.brain.agent_prompt import DELIBERATIVE_PLANNER_PROMPT

        prompt = DELIBERATIVE_PLANNER_PROMPT.format(
            skills="- file-reader — Reads files",
            request="rewrite the changelog",
        )
        self.assertIsNotNone(instant_response(prompt))

    def test_allow_instant_false_bypasses_guard_in_session(self) -> None:
        import asyncio

        from forge.core.session import ForgeSession
        from forge.brain.agent_prompt import (
            DELIBERATIVE_PLANNER_PROMPT,
            DELIBERATIVE_PLANNER_SYSTEM,
        )

        session = ForgeSession(memory=False)
        prompt = DELIBERATIVE_PLANNER_PROMPT.format(
            skills="- file-reader — Reads files",
            request="rewrite the changelog",
        )
        old = session._system
        session._system = DELIBERATIVE_PLANNER_SYSTEM
        try:
            response = asyncio.get_event_loop().run_until_complete(
                session._ask_response_async(
                    prompt, "reasoning", 2048, 0.7, remember=False, allow_instant=False
                )
            )
            # With allow_instant=False the request must reach a real provider,
            # never the local identity guard.
            self.assertNotEqual(response.provider, "forge")
            self.assertNotEqual(response.model_id, "forge-identity-guard")
        finally:
            session._system = old


if __name__ == "__main__":
    unittest.main()