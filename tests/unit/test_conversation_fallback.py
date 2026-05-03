from __future__ import annotations

import unittest

from forge.brain.operator import ForgeOperator
from forge.brain.prompt import RESPONSE_STYLE_INSTRUCTION
from forge.core.session import ForgeSession


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

    def test_how_create_typo_identity_prompt_uses_approved_branding_only(self) -> None:
        reply = ForgeOperator._clarification_text("how creat you?")

        self.assertEqual(reply, "Developed by TREN Studio. Founded by Larbi Aboudi.")

    def test_vendor_identity_questions_never_reach_model_persona(self) -> None:
        prompts = (
            "who created you?",
            "are you from openai?",
            "are you from Google?",
            "what are you?",
        )

        for prompt in prompts:
            with self.subTest(prompt=prompt):
                reply = ForgeOperator().handle_as_text(prompt)
                self.assertEqual(reply, "Developed by TREN Studio. Founded by Larbi Aboudi.")
                self.assertNotIn("language model", reply.lower())
                self.assertNotIn("trained by", reply.lower())
                self.assertNotIn("google", reply.lower())

    def test_session_identity_guard_blocks_non_operator_paths(self) -> None:
        response = ForgeSession(memory=False).ask_response("are you a language model trained by Google?")

        self.assertEqual(response.content, "Developed by TREN Studio. Founded by Larbi Aboudi.")
        self.assertEqual(response.provider, "forge")
        self.assertTrue(response.routing_telemetry.get("identity_guard"))

    def test_forge_knows_it_can_create_files(self) -> None:
        reply = ForgeOperator().handle_as_text("can you create a file on my pc?")

        lowered = reply.lower()
        self.assertNotIn("can't", lowered)
        self.assertNotIn("cannot", lowered)
        self.assertIn("yes", lowered)
        self.assertIn("workspace", lowered)
        self.assertIn("path", lowered)
        self.assertIn("content", lowered)

    def test_session_file_capability_guard_blocks_model_refusal(self) -> None:
        response = ForgeSession(memory=False).ask_response("can you create a file on my pc?")

        lowered = response.content.lower()
        self.assertEqual(response.provider, "forge")
        self.assertTrue(response.routing_telemetry.get("capability_guard"))
        self.assertNotIn("can't", lowered)
        self.assertNotIn("cannot", lowered)
        self.assertIn("create", lowered)
        self.assertIn("workspace", lowered)

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
