from __future__ import annotations

import json
import time
import unittest
from pathlib import Path

from forge.desktop.runtime import stream_prompt


class LiveStreamingEventsTests(unittest.TestCase):
    def test_execution_stream_emits_progress_before_final_result(self) -> None:
        workspace = Path(".forge_artifacts/unit_live_streaming_events").resolve()
        workspace.mkdir(parents=True, exist_ok=True)
        target = workspace / "live_stream_report.md"
        if target.exists():
            target.unlink()

        started = time.monotonic()
        stream = stream_prompt(
            "create live_stream_report.md with content hello streaming",
            confirmed=True,
            workspace_root=workspace,
        )
        first_event = next(stream)
        first_ms = (time.monotonic() - started) * 1000
        events = [first_event, *list(stream)]
        event_types = [event.get("type") for event in events]

        self.assertLess(first_ms, 500)
        self.assertEqual(event_types[0], "status")
        self.assertIn("plan", event_types)
        self.assertIn("step_start", event_types)
        self.assertIn("step_done", event_types)
        self.assertIn("result", event_types)
        self.assertIn("user_response", event_types)
        self.assertIn("technical_details", event_types)
        self.assertIn("done", event_types)
        self.assertTrue(target.exists())

        plan_ready_event = next(event for event in events if event.get("type") == "plan_ready")
        self.assertFalse(plan_ready_event.get("visible", True))

        done_event = next(event for event in events if event.get("type") == "done")
        self.assertNotIn("payload", done_event)
        self.assertEqual(
            set(done_event),
            {"type", "done", "user_response", "has_details", "footer"},
        )

        visible_events = [event for event in events if event.get("type") != "technical_details"]
        visible_text = json.dumps(visible_events, ensure_ascii=False).lower()
        for forbidden in ("nvidia", "deepseek", "worker_lanes", "mission_trace", "provider_telemetry"):
            self.assertNotIn(forbidden, visible_text)


if __name__ == "__main__":
    unittest.main()
