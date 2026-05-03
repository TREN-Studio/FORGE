from __future__ import annotations

import time
import unittest
from pathlib import Path

from forge.cli.main import _instant_cli_response
from forge.cli.main import _is_system_workspace_candidate
from forge.core.identity import FORGE_IDENTITY_RESPONSE, instant_response
from forge.core.router import classify_query_speed, timeout_for_prompt
from forge.core.session import ForgeSession
from forge.desktop.runtime import (
    resolve_path_from_prompt,
    _should_allow_real_changes_for_prompt,
    operate_prompt,
    stream_prompt,
)


class FastPathAndDesktopExecutionTests(unittest.TestCase):
    def test_identity_fast_path_never_calls_provider(self) -> None:
        started = time.monotonic()
        response = ForgeSession(memory=False).ask_response("how creat you?")
        elapsed_ms = (time.monotonic() - started) * 1000

        self.assertLess(elapsed_ms, 500)
        self.assertEqual(response.content, FORGE_IDENTITY_RESPONSE)
        self.assertEqual(response.provider, "forge")
        self.assertNotIn("google", response.content.lower())
        self.assertNotIn("trained by", response.content.lower())

    def test_cli_fast_path_catches_identity_before_operator_boot(self) -> None:
        self.assertEqual(_instant_cli_response("how creat you?"), FORGE_IDENTITY_RESPONSE)
        self.assertIn("FORGE", _instant_cli_response("hi") or "")

    def test_short_greetings_and_tests_are_local(self) -> None:
        self.assertIn("FORGE", instant_response("hi") or "")
        self.assertEqual(instant_response("test"), "FORGE is running. Give me a task.")

    def test_file_capability_does_not_refuse_workspace_access(self) -> None:
        response = ForgeSession(memory=False).ask_response("can you create a file on my pc?")
        lowered = response.content.lower()

        self.assertEqual(response.provider, "forge")
        self.assertNotIn("can't", lowered)
        self.assertNotIn("cannot", lowered)
        self.assertIn("create", lowered)
        self.assertIn("workspace", lowered)

    def test_single_file_create_without_content_asks_for_content_locally(self) -> None:
        reply = instant_response("create a file on my desktop named Illa.txt") or ""

        self.assertIn("What content", reply)
        self.assertIn("Illa.txt".lower(), reply.lower())
        self.assertNotIn("can't", reply.lower())

    def test_single_file_create_with_content_is_not_intercepted(self) -> None:
        self.assertIsNone(instant_response("create hello.txt on my desktop with content: Hello from FORGE"))
        self.assertIsNone(instant_response("create hello.txt on my desktop with hello world"))

    def test_explicit_file_create_auto_confirms_real_workspace_write(self) -> None:
        workspace = Path(".forge_artifacts/unit_fast_path_workspace").resolve()
        workspace.mkdir(parents=True, exist_ok=True)
        target = workspace / "fast_path_note.txt"
        target.unlink(missing_ok=True)

        result = operate_prompt(
            "create fast_path_note.txt with content hello fast path",
            confirmed=False,
            dry_run=False,
            workspace_root=workspace,
        )

        self.assertTrue(target.exists())
        self.assertIn("hello fast path", target.read_text(encoding="utf-8"))
        self.assertEqual(result["validation_status"], "finished")
        output = result["step_results"][0]["output"]
        self.assertTrue(output["verified"])

    def test_content_prefix_is_not_written_as_file_body(self) -> None:
        workspace = Path(".forge_artifacts/unit_content_prefix_workspace").resolve()
        workspace.mkdir(parents=True, exist_ok=True)
        target = workspace / "prefix_note.txt"
        target.unlink(missing_ok=True)

        operate_prompt(
            "create prefix_note.txt with content: Hello from FORGE",
            confirmed=False,
            dry_run=False,
            workspace_root=workspace,
        )

        self.assertEqual(target.read_text(encoding="utf-8"), "Hello from FORGE")

    def test_plain_with_content_writes_file_body(self) -> None:
        workspace = Path(".forge_artifacts/unit_plain_with_workspace").resolve()
        workspace.mkdir(parents=True, exist_ok=True)
        target = workspace / "plain_with_note.txt"
        target.unlink(missing_ok=True)

        result = operate_prompt(
            "create plain_with_note.txt with hello world",
            confirmed=False,
            dry_run=False,
            workspace_root=workspace,
        )

        self.assertTrue(target.exists())
        self.assertEqual(target.read_text(encoding="utf-8"), "hello world")
        self.assertEqual(result["validation_status"], "finished")
        self.assertTrue(result["step_results"][0]["output"]["verified"])

    def test_stream_short_prompt_finishes_without_provider_details(self) -> None:
        events = list(stream_prompt("hi"))
        event_types = [event.get("type") for event in events]
        visible = str([event for event in events if event.get("type") != "technical_details"]).lower()

        self.assertEqual(event_types[0], "status")
        self.assertIn("user_response", event_types)
        self.assertIn("done", event_types)
        self.assertNotIn("google", visible)
        self.assertNotIn("trained by", visible)
        self.assertNotIn("provider_telemetry", visible)

    def test_desktop_real_change_detection_is_conservative(self) -> None:
        self.assertTrue(_should_allow_real_changes_for_prompt("create a file on my desktop named Illa.txt"))
        self.assertTrue(_should_allow_real_changes_for_prompt("create hello.txt in Documents with content ok"))
        self.assertTrue(_should_allow_real_changes_for_prompt("write report.md with content ok"))
        self.assertFalse(_should_allow_real_changes_for_prompt("delete report.md on my desktop"))
        self.assertFalse(_should_allow_real_changes_for_prompt("what can you do?"))

    def test_prompt_path_resolution_supports_common_user_locations(self) -> None:
        desktop = resolve_path_from_prompt("create hello.txt on my desktop with content hi")
        documents = resolve_path_from_prompt("create hello.txt in Documents with content hi")

        if (Path.home() / "Desktop").exists():
            self.assertEqual(desktop, (Path.home() / "Desktop").resolve())
        if (Path.home() / "Documents").exists():
            self.assertEqual(documents, (Path.home() / "Documents").resolve())

    def test_cli_does_not_use_system32_as_default_workspace(self) -> None:
        self.assertTrue(_is_system_workspace_candidate(Path(r"C:\Windows\System32")))
        self.assertFalse(_is_system_workspace_candidate(Path.home()))

    def test_provider_timeout_classification(self) -> None:
        self.assertEqual(classify_query_speed("hello there"), "fast_queries")
        self.assertEqual(timeout_for_prompt("hello there"), 8.0)
        self.assertEqual(classify_query_speed("analyze this website and summarize the key points for me"), "normal_queries")
        self.assertEqual(
            classify_query_speed(" ".join(["complex"] * 31)),
            "complex_queries",
        )

    def test_desktop_ui_has_thinking_timer_copy(self) -> None:
        server_path = Path(__file__).resolve().parents[2] / "forge" / "desktop" / "server.py"
        source = server_path.read_text(encoding="utf-8")

        self.assertIn("startThinkingTimer", source)
        self.assertIn("Thinking... ", source)
        self.assertIn("Taking longer than usual... still working", source)
        self.assertIn("Switching route... please wait", source)


if __name__ == "__main__":
    unittest.main()
