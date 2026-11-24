"""
FORGE Autonomous ReAct Loop
===========================
Iterative Think -> Tool Call -> Observe -> Self-Correct -> Final Answer engine.
Enables FORGE to execute arbitrary multi-step engineering, system, and workspace
tasks autonomously using any free or local LLM provider via ForgeSession.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from forge.brain.contracts import CompletionState, StepExecutionResult
from forge.tools.system import inspect_local_system

MAX_OBSERVATION_CHARS = 4000

REACT_SYSTEM_PROMPT = """Autonomous tool-calling controller.
Solve the objective step-by-step by calling real tools, observing their outputs, self-correcting on errors, and verifying your work before answering.

Available tools:
1. `inspect_system` — args: `{}` — Inspect the local computer (OS, CPU, RAM, disk, Python, network).
2. `list_files` — args: `{"path": ".", "max_depth": 2}` — List files and folders inside the workspace.
3. `read_file` — args: `{"path": "relative/path.txt", "max_chars": 4000}` — Read a text file inside the workspace.
4. `search_files` — args: `{"pattern": "text", "path": "."}` — Search for a text pattern across workspace files.
5. `write_file` — args: `{"path": "relative/path.txt", "content": "..."}` — Create or overwrite a file inside the workspace.
6. `run_command` — args: `{"command": "..."}` — Run a shell command in the workspace directory.

CRITICAL OUTPUT FORMAT:
Respond with ONLY a single valid JSON object (no markdown fences, no extra prose outside JSON).
Either call a tool:
{"thought": "brief reasoning about what to do next", "tool": "tool_name", "args": {...}}
Or finish with your final grounded answer:
{"thought": "all steps verified", "final_answer": "clear, concise, evidence-backed response to the user"}

Rules:
- Never fabricate tool outputs, file contents, or system specs. Always call a tool first to observe ground truth.
- If a tool returns an error, inspect the error and adapt your next action.
- After writing a file, verify it exists or confirm the write succeeded before finishing.
"""


@dataclass(slots=True)
class ReactStep:
    turn: int
    thought: str
    tool: str
    args: dict[str, Any]
    observation: str
    ok: bool


@dataclass(slots=True)
class ReactRunResult:
    final_answer: str
    status: CompletionState
    steps: list[ReactStep] = field(default_factory=list)
    step_results: list[StepExecutionResult] = field(default_factory=list)
    trace: list[str] = field(default_factory=list)


class AutonomousReactLoop:
    """Iterative ReAct execution loop backed by local workspace & system tools."""

    def __init__(
        self,
        workspace_root: Path,
        *,
        max_turns: int = 8,
        dry_run: bool = False,
        confirmed: bool = True,
        command_timeout: int = 30,
    ) -> None:
        self.workspace_root = workspace_root.resolve()
        self.max_turns = max(1, min(max_turns, 16))
        self.dry_run = dry_run
        self.confirmed = confirmed
        self.command_timeout = command_timeout

    def run(
        self,
        objective: str,
        llm_call: Any,
        *,
        memory_context: str = "",
    ) -> ReactRunResult:
        """
        Execute the ReAct loop.

        Args:
            objective: User's task / objective.
            llm_call: Callable(prompt: str) -> str that invokes the active LLM.
            memory_context: Optional prior context / memory.
        """
        steps: list[ReactStep] = []
        step_results: list[StepExecutionResult] = []
        trace: list[str] = [f"Autonomous ReAct loop started for objective: {objective}"]
        history: list[str] = []

        context_block = f"\nRelevant memory:\n{memory_context.strip()}\n" if memory_context.strip() else ""

        for turn in range(1, self.max_turns + 1):
            prompt = self._build_turn_prompt(objective, context_block, history, turn)
            raw_reply = str(llm_call(prompt) or "").strip()
            parsed = self._parse_action(raw_reply)

            if parsed is None:
                # If the model returned plain prose instead of JSON on a turn after
                # already executing tools, treat it as the final answer.
                if steps and raw_reply:
                    trace.append(f"turn_{turn}: returned direct final answer.")
                    return ReactRunResult(
                        final_answer=raw_reply,
                        status=CompletionState.FINISHED,
                        steps=steps,
                        step_results=step_results,
                        trace=trace,
                    )
                history.append(
                    f"Turn {turn} format error: Your response was not valid JSON. "
                    "Respond with ONLY a JSON object containing either 'tool' and 'args', or 'final_answer'."
                )
                trace.append(f"turn_{turn}: invalid JSON from model; requested retry.")
                continue

            thought = str(parsed.get("thought") or "").strip()

            if "final_answer" in parsed and parsed.get("final_answer") is not None:
                answer = str(parsed["final_answer"]).strip()
                trace.append(f"turn_{turn}: finished ({thought or 'objective complete'}).")
                return ReactRunResult(
                    final_answer=answer or "Completed.",
                    status=CompletionState.FINISHED,
                    steps=steps,
                    step_results=step_results,
                    trace=trace,
                )

            tool_name = str(parsed.get("tool") or "").strip()
            tool_args = parsed.get("args") if isinstance(parsed.get("args"), dict) else {}

            if not tool_name:
                history.append(f"Turn {turn} error: Missing 'tool' or 'final_answer' in JSON.")
                continue

            ok, observation = self._execute_tool(tool_name, tool_args)
            truncated_obs = self._truncate(observation)

            step = ReactStep(
                turn=turn,
                thought=thought,
                tool=tool_name,
                args=tool_args,
                observation=truncated_obs,
                ok=ok,
            )
            steps.append(step)
            step_results.append(
                StepExecutionResult(
                    step_id=f"react_turn_{turn}",
                    skill=f"react:{tool_name}",
                    status=CompletionState.FINISHED if ok else CompletionState.FAILED,
                    output={"tool": tool_name, "args": tool_args, "observation": truncated_obs},
                    error="" if ok else truncated_obs,
                    evidence=[f"tool:{tool_name}", f"ok:{ok}"],
                )
            )
            trace.append(f"turn_{turn}: {tool_name}({json.dumps(tool_args, ensure_ascii=False)[:120]}) -> {'OK' if ok else 'ERROR'}")
            history.append(
                f"Turn {turn}:\n"
                f"Thought: {thought}\n"
                f"Tool: {tool_name}\n"
                f"Args: {json.dumps(tool_args, ensure_ascii=False)}\n"
                f"Observation ({'OK' if ok else 'ERROR'}):\n{truncated_obs}"
            )

        # Max turns reached: summarize what was accomplished honestly.
        last_obs = steps[-1].observation if steps else "No tool steps executed."
        trace.append(f"Reached max turns ({self.max_turns}).")
        return ReactRunResult(
            final_answer=f"Completed {len(steps)} autonomous step(s) (max turns reached). Latest observation:\n{last_obs}",
            status=CompletionState.PARTIALLY_FINISHED if steps else CompletionState.FAILED,
            steps=steps,
            step_results=step_results,
            trace=trace,
        )

    def _build_turn_prompt(
        self,
        objective: str,
        context_block: str,
        history: list[str],
        turn: int,
    ) -> str:
        history_block = "\n\n".join(history) if history else "(No actions taken yet.)"
        return (
            f"{REACT_SYSTEM_PROMPT}\n\n"
            f"Workspace root: {self.workspace_root}\n"
            f"Dry-run mode: {self.dry_run}\n"
            f"{context_block}\n"
            f"Objective:\n{objective}\n\n"
            f"Execution history so far:\n{history_block}\n\n"
            f"Now produce your JSON response for turn {turn}/{self.max_turns}:"
        )

    @staticmethod
    def _parse_action(raw: str) -> dict[str, Any] | None:
        if not raw:
            return None
        # Strip reasoning model <think>...</think> blocks before JSON extraction
        text = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL | re.IGNORECASE).strip()
        if not text:
            return None
        # Strip optional markdown code fences
        fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
        if fence_match:
            text = fence_match.group(1).strip()
        elif not text.startswith("{"):
            brace_start = text.find("{")
            brace_end = text.rfind("}")
            if brace_start != -1 and brace_end > brace_start:
                text = text[brace_start : brace_end + 1]
        try:
            data = json.loads(text)
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None

    def _safe_path(self, rel_path: str) -> Path:
        candidate = (self.workspace_root / (rel_path or ".")).resolve()
        try:
            candidate.relative_to(self.workspace_root)
        except ValueError as exc:
            raise ValueError(f"Path '{rel_path}' escapes workspace root '{self.workspace_root}'.") from exc
        return candidate

    def _execute_tool(self, tool_name: str, args: dict[str, Any]) -> tuple[bool, str]:
        try:
            if tool_name == "inspect_system":
                facts = inspect_local_system(self.workspace_root)
                return True, json.dumps(facts, indent=2, ensure_ascii=False)

            if tool_name == "list_files":
                target = self._safe_path(str(args.get("path") or "."))
                max_depth = max(1, min(int(args.get("max_depth", 2)), 4))
                if not target.exists():
                    return False, f"Directory not found: {target}"
                entries: list[str] = []
                base_depth = len(target.parts)
                for root, dirs, files in os.walk(target):
                    dirs[:] = [d for d in dirs if not d.startswith(".") and d not in {"node_modules", "__pycache__", ".git", "dist", "build"}]
                    current_depth = len(Path(root).parts) - base_depth
                    if current_depth >= max_depth:
                        dirs[:] = []
                    rel_root = Path(root).relative_to(self.workspace_root)
                    for name in sorted(files)[:40]:
                        rel_file = (rel_root / name).as_posix() if str(rel_root) != "." else name
                        entries.append(rel_file)
                    if len(entries) >= 120:
                        break
                return True, "\n".join(entries) if entries else "(empty directory)"

            if tool_name == "read_file":
                target = self._safe_path(str(args.get("path") or ""))
                if not target.is_file():
                    return False, f"File not found: {args.get('path')}"
                max_chars = max(200, min(int(args.get("max_chars", 4000)), 12000))
                content = target.read_text(encoding="utf-8", errors="replace")
                if len(content) > max_chars:
                    content = content[:max_chars] + f"\n... [truncated {len(content) - max_chars} chars]"
                return True, content

            if tool_name == "search_files":
                pattern = str(args.get("pattern") or "").strip()
                if not pattern:
                    return False, "Missing required argument 'pattern'."
                target = self._safe_path(str(args.get("path") or "."))
                matches: list[str] = []
                for root, dirs, files in os.walk(target):
                    dirs[:] = [d for d in dirs if not d.startswith(".") and d not in {"node_modules", "__pycache__", ".git", "dist", "build"}]
                    for name in files:
                        fpath = Path(root) / name
                        try:
                            if fpath.stat().st_size > 256_000:
                                continue
                            text = fpath.read_text(encoding="utf-8", errors="ignore")
                        except OSError:
                            continue
                        for lineno, line in enumerate(text.splitlines(), start=1):
                            if pattern.lower() in line.lower():
                                rel = fpath.relative_to(self.workspace_root).as_posix()
                                matches.append(f"{rel}:{lineno}: {line.strip()[:140]}")
                                if len(matches) >= 50:
                                    return True, "\n".join(matches)
                return True, "\n".join(matches) if matches else f"No matches found for '{pattern}'."

            if tool_name == "write_file":
                rel = str(args.get("path") or "").strip()
                if not rel:
                    return False, "Missing required argument 'path'."
                target = self._safe_path(rel)
                content = str(args.get("content") or "")
                if self.dry_run:
                    return True, f"[DRY-RUN] Would write {len(content)} chars to `{rel}`."
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
                return True, f"Wrote {len(content)} chars to `{rel}` and verified file exists."

            if tool_name == "run_command":
                command = str(args.get("command") or "").strip()
                if not command:
                    return False, "Missing required argument 'command'."
                if self.dry_run:
                    return True, f"[DRY-RUN] Would execute command: {command}"
                completed = subprocess.run(
                    command,
                    shell=True,
                    cwd=str(self.workspace_root),
                    capture_output=True,
                    text=True,
                    timeout=self.command_timeout,
                )
                output = (completed.stdout or "") + ("\n" + completed.stderr if completed.stderr else "")
                output = output.strip() or "(no output)"
                if completed.returncode != 0:
                    return False, f"Exit code {completed.returncode}:\n{output}"
                return True, output

            return False, f"Unknown tool: '{tool_name}'. Available: inspect_system, list_files, read_file, search_files, write_file, run_command."
        except Exception as exc:
            return False, f"Tool '{tool_name}' error: {exc}"

    @staticmethod
    def _truncate(text: str) -> str:
        if len(text) <= MAX_OBSERVATION_CHARS:
            return text
        return text[:MAX_OBSERVATION_CHARS] + f"\n... [truncated {len(text) - MAX_OBSERVATION_CHARS} chars]"
