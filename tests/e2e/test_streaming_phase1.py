from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from forge.brain.orchestrator import MissionOrchestrator
from forge.desktop.runtime import stream_prompt


def _close_shared_workers() -> None:
    if MissionOrchestrator._shared_workers is not None:
        MissionOrchestrator._shared_workers.close()
    MissionOrchestrator._shared_workers = None
    MissionOrchestrator._shared_approval_engine = None


class StreamingPhase1E2ETest(unittest.TestCase):
    def tearDown(self) -> None:
        _close_shared_workers()

    def test_execution_stream_emits_lifecycle_events_within_500ms(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as workspace:
            started = time.monotonic()
            events: list[tuple[float, dict]] = []
            try:
                for event in stream_prompt(
                    "Create notes.txt with content hello streaming",
                    confirmed=True,
                    workspace_root=workspace,
                ):
                    events.append((time.monotonic() - started, event))
                    if event.get("type") == "done":
                        break
            finally:
                _close_shared_workers()

            self.assertTrue(events, "stream_prompt should emit at least one event")
            self.assertLessEqual(events[0][0], 0.5, "first streaming event should appear within 500ms")

            event_types = [event.get("type") for _, event in events]
            self.assertIn("intent_analyzing", event_types)
            self.assertIn("plan_ready", event_types)
            self.assertIn("provider_selected", event_types)
            self.assertIn("step_started", event_types)
            self.assertIn("step_completed", event_types)
            self.assertIn("mission_completed", event_types)
            self.assertIn("done", event_types)

            mission_completed = next(event for _, event in events if event.get("type") == "mission_completed")
            self.assertTrue(mission_completed.get("success"))
            self.assertGreaterEqual(float(mission_completed.get("total_latency_ms") or 0), 0)
            self.assertTrue(mission_completed.get("final_provider"))

            output_file = Path(workspace) / "notes.txt"
            self.assertTrue(output_file.exists(), "streamed execution should create the requested file")
            self.assertIn("hello streaming", output_file.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
