from __future__ import annotations

import json
from pathlib import Path

from forge.brain.contracts import CompletionState
from forge.brain.react_loop import AutonomousReactLoop


def test_react_loop_write_read_verify(tmp_path: Path) -> None:
    turns = iter(
        [
            json.dumps(
                {
                    "thought": "Create hello.txt with the requested text.",
                    "tool": "write_file",
                    "args": {"path": "hello.txt", "content": "Hello FORGE!"},
                }
            ),
            json.dumps(
                {
                    "thought": "Read back hello.txt to verify its content.",
                    "tool": "read_file",
                    "args": {"path": "hello.txt"},
                }
            ),
            json.dumps(
                {
                    "thought": "Verified file content matches.",
                    "final_answer": "Created hello.txt with 'Hello FORGE!' and verified its content.",
                }
            ),
        ]
    )

    loop = AutonomousReactLoop(workspace_root=tmp_path, max_turns=5)
    result = loop.run("Create hello.txt and verify it", lambda _prompt: next(turns))

    assert result.status == CompletionState.FINISHED
    assert len(result.steps) == 2
    assert result.steps[0].tool == "write_file"
    assert result.steps[0].ok is True
    assert result.steps[1].tool == "read_file"
    assert "Hello FORGE!" in result.steps[1].observation
    assert (tmp_path / "hello.txt").read_text(encoding="utf-8") == "Hello FORGE!"
    assert "Created hello.txt" in result.final_answer


def test_react_loop_self_corrects_on_missing_file(tmp_path: Path) -> None:
    (tmp_path / "actual_notes.txt").write_text("Secret code: 42", encoding="utf-8")

    turns = iter(
        [
            json.dumps(
                {
                    "thought": "Try reading notes.txt first.",
                    "tool": "read_file",
                    "args": {"path": "notes.txt"},
                }
            ),
            json.dumps(
                {
                    "thought": "notes.txt was missing; list files to find the real filename.",
                    "tool": "list_files",
                    "args": {"path": "."},
                }
            ),
            json.dumps(
                {
                    "thought": "Found actual_notes.txt; read it now.",
                    "tool": "read_file",
                    "args": {"path": "actual_notes.txt"},
                }
            ),
            json.dumps(
                {
                    "thought": "Got the code.",
                    "final_answer": "The code in actual_notes.txt is 42.",
                }
            ),
        ]
    )

    loop = AutonomousReactLoop(workspace_root=tmp_path, max_turns=6)
    result = loop.run("Find the secret code in the notes file", lambda _prompt: next(turns))

    assert result.status == CompletionState.FINISHED
    assert result.steps[0].ok is False
    assert result.steps[1].ok is True
    assert "actual_notes.txt" in result.steps[1].observation
    assert result.steps[2].ok is True
    assert "42" in result.final_answer


def test_react_loop_blocks_path_traversal(tmp_path: Path) -> None:
    turns = iter(
        [
            json.dumps(
                {
                    "thought": "Try to read outside workspace.",
                    "tool": "read_file",
                    "args": {"path": "../../outside_secret.txt"},
                }
            ),
            json.dumps(
                {
                    "thought": "Blocked by sandbox.",
                    "final_answer": "Cannot access files outside the workspace.",
                }
            ),
        ]
    )

    loop = AutonomousReactLoop(workspace_root=tmp_path, max_turns=4)
    result = loop.run("Read ../../outside_secret.txt", lambda _prompt: next(turns))

    assert result.status == CompletionState.FINISHED
    assert result.steps[0].ok is False
    assert "escapes workspace root" in result.steps[0].observation


def test_react_loop_respects_dry_run(tmp_path: Path) -> None:
    turns = iter(
        [
            json.dumps(
                {
                    "thought": "Write file in dry-run mode.",
                    "tool": "write_file",
                    "args": {"path": "should_not_exist.txt", "content": "nope"},
                }
            ),
            json.dumps(
                {
                    "thought": "Done dry-run.",
                    "final_answer": "Dry-run completed without modifying disk.",
                }
            ),
        ]
    )

    loop = AutonomousReactLoop(workspace_root=tmp_path, max_turns=4, dry_run=True)
    result = loop.run("Create should_not_exist.txt", lambda _prompt: next(turns))

    assert result.status == CompletionState.FINISHED
    assert "[DRY-RUN]" in result.steps[0].observation
    assert not (tmp_path / "should_not_exist.txt").exists()


def test_react_loop_inspect_system(tmp_path: Path) -> None:
    turns = iter(
        [
            json.dumps(
                {
                    "thought": "Inspect local system specs.",
                    "tool": "inspect_system",
                    "args": {},
                }
            ),
            json.dumps(
                {
                    "thought": "Summarize observed specs.",
                    "final_answer": "System inspection complete.",
                }
            ),
        ]
    )

    loop = AutonomousReactLoop(workspace_root=tmp_path, max_turns=4)
    result = loop.run("Inspect this computer", lambda _prompt: next(turns))

    assert result.status == CompletionState.FINISHED
    assert result.steps[0].ok is True
    assert "platform" in result.steps[0].observation
    assert "cpu_count_logical" in result.steps[0].observation
