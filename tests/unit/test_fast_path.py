from __future__ import annotations

import time
import unittest

from forge.brain.identity_guard import get_instant_response, is_identity_question, sanitize_response


class IdentityGuardFastPathTests(unittest.TestCase):
    def test_hi_returns_instantly(self) -> None:
        started = time.monotonic()
        instant = get_instant_response("hi")
        elapsed_ms = (time.monotonic() - started) * 1000

        self.assertIsNotNone(instant)
        self.assertLess(elapsed_ms, 300)
        self.assertIn("FORGE", instant["user_response"])
        self.assertTrue(instant["technical_details"]["instant_response"])

    def test_typo_identity_uses_forge_brand(self) -> None:
        instant = get_instant_response("how creat you?")

        self.assertIsNotNone(instant)
        self.assertEqual(instant["user_response"], "Developed by TREN Studio. Founded by Larbi Aboudi.")
        self.assertTrue(is_identity_question("how creat you?"))

    def test_file_capability_affirms_agent_ability(self) -> None:
        instant = get_instant_response("can you create a file?")

        self.assertIsNotNone(instant)
        lowered = instant["user_response"].lower()
        self.assertIn("yes", lowered)
        self.assertIn("create", lowered)
        self.assertNotIn("can't", lowered)

    def test_sanitize_blocks_model_identity_leaks(self) -> None:
        cleaned = sanitize_response("I am a large language model, trained by Google.")

        self.assertEqual(cleaned, "Developed by TREN Studio. Founded by Larbi Aboudi.")


if __name__ == "__main__":
    unittest.main()
