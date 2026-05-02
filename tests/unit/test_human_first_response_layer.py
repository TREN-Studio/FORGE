from __future__ import annotations

import unittest

from forge.brain.contracts import CompletionState
from forge.desktop.runtime import _humanize_visible_response, _stream_footer, _with_human_first_response


class HumanFirstResponseLayerTests(unittest.TestCase):
    def test_visible_response_strips_console_internals(self) -> None:
        raw = """
Useful user-facing answer.

[worker_lanes]
provider: nvidia/deepseek-v3.2
mission_trace: ["internal"]
agent_reviews: [{"agent":"critic"}]
"""
        visible = _humanize_visible_response(raw, {})

        self.assertIn("Useful user-facing answer", visible)
        self.assertNotIn("worker_lanes", visible)
        self.assertNotIn("nvidia", visible.lower())
        self.assertNotIn("deepseek", visible.lower())
        self.assertNotIn("mission_trace", visible)

    def test_payload_keeps_details_hidden_and_answer_clean(self) -> None:
        payload = {
            "answer": "Done.\n\nprovider: nvidia/deepseek-v3.2\nmission_trace: hidden",
            "result": "raw result",
            "validation_status": CompletionState.FINISHED.value,
            "intent": {"primary_intent": "automation"},
            "plan": {"steps": []},
            "step_results": [],
            "mission_trace": ["internal trace"],
            "agent_reviews": [{"agent": "critic"}],
            "provider_telemetry": {"final_provider_used": "nvidia", "fallback_count": 0},
            "artifacts": {"report": {"summary": "Created report.md"}},
        }

        result = _with_human_first_response(payload)

        self.assertEqual(result["answer"], result["user_response"])
        self.assertTrue(result["has_technical_details"])
        self.assertIn("provider_telemetry", result["technical_details"])
        self.assertNotIn("nvidia", result["answer"].lower())
        self.assertNotIn("mission_trace", result["answer"])

    def test_file_edit_response_summarizes_without_diff(self) -> None:
        visible = _humanize_visible_response(
            "[step_1]\n\nApplied create on `report.md`. Change detected.\n\n"
            "--- a/report.md\n+++ b/report.md\n@@ -0,0 +1 @@\n+raw line\n\n"
            "Status: finished. Steps completed: 1/1.",
            {},
        )

        self.assertIn("I created `report.md`", visible)
        self.assertNotIn("--- a/report.md", visible)
        self.assertNotIn("@@", visible)
        self.assertNotIn("Status:", visible)

    def test_stream_footer_hides_provider_names(self) -> None:
        footer = _stream_footer(
            {
                "provider_used": "nvidia",
                "model_used": "deepseek-v3.2",
                "provider_telemetry": {
                    "final_provider_used": "nvidia",
                    "provider_latency_ms": 1200,
                    "fallback_count": 1,
                },
            },
            elapsed_ms=1400,
        )

        self.assertIn("FORGE", footer)
        self.assertNotIn("nvidia", footer.lower())
        self.assertNotIn("deepseek", footer.lower())
        self.assertNotIn("fallback", footer.lower())


if __name__ == "__main__":
    unittest.main()
