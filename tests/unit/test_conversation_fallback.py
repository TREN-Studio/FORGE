from __future__ import annotations

import unittest

from forge.brain.operator import ForgeOperator


class ConversationFallbackTests(unittest.TestCase):
    def test_friendly_chat_prompt_does_not_reject_user(self) -> None:
        reply = ForgeOperator._clarification_text("CAN SPEAK TO ME")

        self.assertIn("I can chat with you", reply)
        self.assertIn("concrete task", reply)
        self.assertIn("plan it, choose tools, execute steps", reply)
        self.assertNotIn("not a general chatbot", reply)

    def test_capability_prompt_suggests_agent_examples(self) -> None:
        reply = ForgeOperator._clarification_text("what can you do?")

        self.assertIn("Try one of these", reply)
        self.assertIn("Create notes.txt", reply)
        self.assertIn("action_items.md", reply)
        self.assertIn("report evidence", reply)

    def test_identity_prompt_uses_approved_branding_only(self) -> None:
        reply = ForgeOperator._clarification_text("who developed you?")

        self.assertIn("developed by TREN Studio", reply)
        self.assertIn("TREN Studio was founded by Larbi Aboudi", reply)
        self.assertNotIn("not a general chatbot", reply)

    def test_ambiguous_prompt_guides_toward_agent_task(self) -> None:
        reply = ForgeOperator._clarification_text("interesting")

        self.assertIn("I can help with that", reply)
        self.assertIn("one concrete task", reply)
        self.assertIn("evidence, and validation", reply)


if __name__ == "__main__":
    unittest.main()
