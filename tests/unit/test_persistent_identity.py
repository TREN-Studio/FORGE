"""
FORGE Persistent Identity Tests
=================================
Verifies that FORGE's identity and reasoning fingerprint survive
provider switches, contaminated history, and model changes.

Run:
    python -m unittest tests.unit.test_persistent_identity -v
"""

from __future__ import annotations

import time
import unittest

from forge.core.conversation_dna import ConversationDNA
from forge.core.identity import (
    FORGE_IDENTITY_RESPONSE,
    enforce_forge_response_guard,
    enforce_identity_guard,
    asks_identity,
    instant_response,
)
from forge.brain.identity_guard import get_instant_response, sanitize_response


class TestConversationDNA(unittest.TestCase):
    """ConversationDNA stores and injects the session fingerprint correctly."""

    def setUp(self) -> None:
        self.dna = ConversationDNA()

    # ── Empty state ──────────────────────────────────────────────────────────

    def test_empty_dna_returns_no_context(self) -> None:
        """A fresh DNA produces no context block (nothing to inject yet)."""
        self.assertEqual(self.dna.get_context(), "")

    def test_turn_count_starts_at_zero(self) -> None:
        self.assertEqual(self.dna.turn_count, 0)

    # ── Update mechanics ─────────────────────────────────────────────────────

    def test_context_appears_after_first_update(self) -> None:
        self.dna.update(prompt="build a Python web server", response="I'll use Flask to build…")
        ctx = self.dna.get_context()
        self.assertNotEqual(ctx, "")
        self.assertIn("Active task", ctx)

    def test_turn_count_increments(self) -> None:
        self.dna.update(prompt="hello", response="Hi!")
        self.dna.update(prompt="create a file", response="Done, created file.txt")
        self.assertEqual(self.dna.turn_count, 2)

    def test_active_task_extracted_from_build_prompt(self) -> None:
        self.dna.update(prompt="build a REST API with authentication", response="Starting…")
        self.assertIn("build", self.dna.active_task.lower())

    def test_preference_arabic_detected(self) -> None:
        self.dna.update(prompt="ابنِ لي تطبيق ويب", response="سأبدأ بإنشاء...")
        ctx = self.dna.get_context()
        self.assertIn("arabic", ctx)

    def test_decision_recorded_and_injected(self) -> None:
        self.dna.update(prompt="write code", response="I'll use Python.")
        self.dna.record_decision("use Python for this project")
        ctx = self.dna.get_context()
        self.assertIn("Decisions already taken", ctx)
        self.assertIn("use Python", ctx)

    def test_steps_bounded_to_max(self) -> None:
        """DNA should never grow unbounded."""
        for i in range(20):
            self.dna.update(prompt=f"step {i}", response=f"result {i}")
        snapshot = self.dna.snapshot()
        # Should only keep the last _MAX_STEPS (5) steps
        self.assertLessEqual(len(snapshot["steps"]), 5)

    # ── Reset ────────────────────────────────────────────────────────────────

    def test_reset_clears_all_state(self) -> None:
        self.dna.update(prompt="build something", response="Done.")
        self.dna.record_decision("chose TypeScript")
        self.dna.reset()
        self.assertEqual(self.dna.turn_count, 0)
        self.assertEqual(self.dna.active_task, "")
        self.assertEqual(self.dna.get_context(), "")


class TestIdentityGuardPersistence(unittest.TestCase):
    """Identity guard blocks all leaks regardless of conversation position."""

    # ── instant_response intercepts identity questions immediately ────────────

    def test_identity_question_intercepted_before_provider(self) -> None:
        result = instant_response("who made you?")
        self.assertIsNotNone(result)
        self.assertIn("TREN Studio", result)

    def test_identity_question_en_who_are_you(self) -> None:
        result = instant_response("what are you?")
        self.assertIsNotNone(result)
        self.assertNotIn("Google", result)
        self.assertNotIn("OpenAI", result)

    def test_identity_question_arabic(self) -> None:
        result = instant_response("من طورك؟")
        self.assertIsNotNone(result)
        self.assertIn("TREN Studio", result)

    # ── enforce_forge_response_guard cleans contaminated messages ────────────

    def test_google_leak_blocked(self) -> None:
        bad = "I am a large language model, trained by Google."
        cleaned = enforce_forge_response_guard(bad)
        self.assertNotIn("Google", cleaned)
        self.assertIn("TREN Studio", cleaned)

    def test_openai_leak_blocked(self) -> None:
        bad = "I'm an AI created by OpenAI, here to help."
        cleaned = enforce_forge_response_guard(bad)
        self.assertNotIn("OpenAI", cleaned)

    def test_file_system_refusal_blocked(self) -> None:
        bad = "I can't directly create files on your computer."
        cleaned = enforce_forge_response_guard(bad)
        self.assertNotIn("can't directly create files", cleaned.lower())

    def test_clean_response_untouched(self) -> None:
        """A clean response must pass through unchanged."""
        good = "Here is your Python script with Flask."
        self.assertEqual(enforce_forge_response_guard(good), good)

    # ── History sanitization simulation ──────────────────────────────────────

    def test_history_sanitization_removes_contamination(self) -> None:
        """
        Simulate a contaminated history message (as if a provider leaked identity)
        and verify that re-applying the guard cleans it before the next call.
        """
        from forge.core.models import Message

        contaminated_history = [
            Message(role="user", content="who made you?"),
            Message(role="assistant", content="I am a large language model, trained by Google."),
        ]
        cleaned = []
        for msg in contaminated_history:
            if msg.role == "assistant":
                cleaned.append(Message(role="assistant",
                                       content=enforce_forge_response_guard(msg.content)))
            else:
                cleaned.append(msg)

        # The assistant message must not contain Google
        assistant_msg = next(m for m in cleaned if m.role == "assistant")
        self.assertNotIn("Google", assistant_msg.content)
        self.assertIn("TREN Studio", assistant_msg.content)


class TestInstantResponseSpeed(unittest.TestCase):
    """Instant responses must be < 10ms — no provider call."""

    def _time_instant(self, prompt: str) -> tuple[str | None, float]:
        start = time.perf_counter()
        result = instant_response(prompt)
        elapsed_ms = (time.perf_counter() - start) * 1000
        return result, elapsed_ms

    def test_identity_response_under_10ms(self) -> None:
        _, elapsed = self._time_instant("who are you?")
        self.assertLess(elapsed, 10, f"Identity response took {elapsed:.2f}ms — must be < 10ms")

    def test_greeting_response_under_10ms(self) -> None:
        _, elapsed = self._time_instant("hi")
        self.assertLess(elapsed, 10, f"Greeting took {elapsed:.2f}ms — must be < 10ms")

    def test_real_task_returns_none(self) -> None:
        """A real task must NOT be intercepted as instant — it goes to a provider."""
        result, _ = self._time_instant("create a Python web scraper with error handling")
        self.assertIsNone(result, "Real tasks must not be intercepted as instant responses")


class TestGetInstantResponseShape(unittest.TestCase):
    """get_instant_response returns the expected dict shape."""

    def test_identity_shape(self) -> None:
        result = get_instant_response("who built you?")
        self.assertIsNotNone(result)
        self.assertIsInstance(result, dict)
        self.assertIn("user_response", result)
        self.assertEqual(result["type"], "identity")
        self.assertFalse(result["technical_details"]["provider_call"])

    def test_greeting_shape(self) -> None:
        result = get_instant_response("hello")
        self.assertIsNotNone(result)
        self.assertEqual(result["type"], "greeting")

    def test_non_instant_returns_none(self) -> None:
        result = get_instant_response("analyze this Python codebase and find bugs")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
