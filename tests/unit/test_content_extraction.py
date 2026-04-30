from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

from forge.brain.planner import PlanningEngine


def _load_file_editor_extract_content():
    executor_path = (
        Path(__file__).resolve().parents[2]
        / "forge"
        / "skills_catalog"
        / "file-editor"
        / "executor.py"
    )
    spec = importlib.util.spec_from_file_location("forge_file_editor_executor", executor_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load file-editor executor from {executor_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._extract_content


extract_file_editor_content = _load_file_editor_extract_content()


class ContentExtractionTests(unittest.TestCase):
    def test_planner_stops_at_next_shell_instruction(self) -> None:
        request = (
            "Create a file named notes.txt with exactly this content:\n"
            "FORGE live verification\n"
            "Then run `python -m compileall .` and report whether both steps succeeded."
        )
        self.assertEqual(PlanningEngine._extract_content(request), "FORGE live verification")

    def test_file_editor_stops_at_next_shell_instruction(self) -> None:
        request = (
            "Create a file named notes.txt with exactly this content:\n"
            "FORGE live verification\n"
            "Then run `python -m compileall .` and report whether both steps succeeded."
        )
        self.assertEqual(extract_file_editor_content(request), "FORGE live verification")

    def test_inline_sentence_boundary_is_removed(self) -> None:
        request = (
            "Create notes.txt with the exact content: "
            "FORGE UI live verification. Then run `python -m compileall .`"
        )
        self.assertEqual(PlanningEngine._extract_content(request), "FORGE UI live verification.")
        self.assertEqual(extract_file_editor_content(request), "FORGE UI live verification.")

    def test_fenced_block_stays_authoritative(self) -> None:
        request = (
            "Write config.md with this content:\n"
            "```text\n"
            "line one\n"
            "line two\n"
            "```\n"
            "Then run `python -m compileall .`."
        )
        expected = "line one\nline two"
        self.assertEqual(PlanningEngine._extract_content(request), expected)
        self.assertEqual(extract_file_editor_content(request), expected)


if __name__ == "__main__":
    unittest.main()
