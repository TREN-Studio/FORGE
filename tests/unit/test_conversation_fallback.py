from __future__ import annotations

import unittest

from forge.brain.operator import ForgeOperator
from forge.brain.prompt import RESPONSE_STYLE_INSTRUCTION


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

        self.assertEqual(reply, "Developed by TREN Studio. Founded by Larbi Aboudi.")
        self.assertNotIn("not a general chatbot", reply)

    def test_typo_identity_prompt_uses_approved_branding_only(self) -> None:
        reply = ForgeOperator._clarification_text("ho creat you?")

        self.assertEqual(reply, "Developed by TREN Studio. Founded by Larbi Aboudi.")

    def test_identity_prompt_with_create_typo_does_not_execute_skill(self) -> None:
        reply = ForgeOperator().handle_as_text("who create u?")

        self.assertEqual(reply, "Developed by TREN Studio. Founded by Larbi Aboudi.")
        self.assertNotIn("step_1", reply)
        self.assertNotIn("dry-run", reply.lower())

    def test_file_creation_prompt_is_not_identity_prompt(self) -> None:
        self.assertFalse(ForgeOperator._asks_identity("create notes.txt with content hello forge"))

    def test_prompt_forbids_invented_forge_identity(self) -> None:
        self.assertIn("Developed by TREN Studio. Founded by Larbi Aboudi.", RESPONSE_STYLE_INSTRUCTION)
        self.assertIn("Never invent company names", RESPONSE_STYLE_INSTRUCTION)

    def test_ambiguous_prompt_guides_toward_agent_task(self) -> None:
        reply = ForgeOperator._clarification_text("interesting")

        self.assertIn("I can help with that", reply)
        self.assertIn("one concrete task", reply)
        self.assertIn("evidence, and validation", reply)


if __name__ == "__main__":
    unittest.main()
