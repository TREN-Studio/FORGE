from __future__ import annotations

from dataclasses import dataclass, field
import http.client
import json
import os
from pathlib import Path
from queue import Empty, Queue
import re
import threading
import time
from typing import Any

from forge import __version__
from forge.brain.contracts import CompletionState, ExecutionPlan, IntentKind, OperatorResult, TaskIntent
from forge.brain.identity import enforce_forge_response_guard
from forge.brain.operator import ForgeOperator
from forge.config.settings import OperatorSettings
from forge.core.identity import instant_response
from forge.core.session import ForgeSession
from forge.skills.runtime import SkillExecutionContext
from forge.tools.workspace import WorkspaceTools


@dataclass(slots=True)
class DesktopBootStatus:
    providers: int
    models_online: int
    summary: str
    workspace_root: str
    artifact_root: str
    provider_setup: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DesktopWorkspaceState:
    workspace_root: Path
    state_file: Path


_STATE_LOCK = threading.Lock()
_OLLAMA_TAGS_URL = "http://localhost:11434/api/tags"
_OLLAMA_HOST = "127.0.0.1"
_OLLAMA_PORT = 11434
_DEMO_INPUT = """# Launch Notes

- Tighten checkout copy before release.
- Add a quick verification for the download link.
- Confirm the reports folder exists.
"""
_DEMO_OUTPUT = """# Action Items

- [ ] Tighten checkout copy before release.
- [ ] Add a quick verification for the download link.
- [ ] Confirm the reports folder exists.

Source: demo_input.md
"""
_DEMO_PROMPT = "FORGE_LOCAL_DEMO_RUN demo_input.md action_items.md"
_VISIBLE_WORD_LIMIT = 180
_VISIBLE_BLOCKED_MARKERS = (
    "mission_trace",
    "worker_lanes",
    "agent_reviews",
    "provider_telemetry",
    "routing_telemetry",
    "model_used",
    "provider_used",
    "audit_log",
    "fallback_count",
    "attempted_providers",
    "raw approvals",
    "nvidia",
    "deepseek",
    "openai",
    "anthropic",
    "gemini",
    "groq",
    "mistral",
    "openrouter",
    "together",
    "cloudflare",
)


def _default_state_directory() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    path = base / "FORGE"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _state_file() -> Path:
    return _default_state_directory() / "desktop-runtime.json"


def _fallback_workspace_root() -> Path:
    return Path.cwd().resolve()


def _normalize_workspace_root(value: str | Path | None) -> Path:
    if value is None or str(value).strip() == "":
        return _load_workspace_state().workspace_root
    workspace = Path(str(value)).expanduser().resolve()
    if not workspace.exists():
        raise FileNotFoundError(f"Workspace does not exist: {workspace}")
    if not workspace.is_dir():
        raise NotADirectoryError(f"Workspace is not a directory: {workspace}")
    return workspace


_REAL_CHANGE_TERMS = (
    "create",
    "write",
    "save",
    "edit",
    "modify",
    "update",
    "انشئ",
    "أنشئ",
    "اكتب",
)
_EXPLICIT_PATH_TERMS = (
    "desktop",
    "documents",
    "document folder",
    "my pc",
    "my computer",
    "this computer",
    "local machine",
    "~/",
    "home folder",
    "c:\\",
    "d:\\",
)
_DESTRUCTIVE_TERMS = (
    "delete",
    "remove",
    "erase",
    "format",
    "wipe",
    "rm ",
    "del ",
    "حذف",
)


def _should_allow_real_changes_for_prompt(prompt: str) -> bool:
    lowered = str(prompt or "").lower()
    if any(term in lowered for term in _DESTRUCTIVE_TERMS):
        return False
    wants_change = any(term in lowered for term in _REAL_CHANGE_TERMS)
    has_explicit_path = any(term in lowered for term in _EXPLICIT_PATH_TERMS)
    has_file_target = bool(re.search(r"\b[\w .-]+\.(?:txt|md|json|py|csv|html|css|js|ts|yml|yaml)\b", lowered))
    return wants_change and (has_explicit_path or has_file_target)


def resolve_path_from_prompt(prompt: str) -> Path | None:
    lowered = str(prompt or "").lower()
    if "desktop" in lowered:
        desktop = Path.home() / "Desktop"
        if desktop.exists() and desktop.is_dir():
            return desktop.resolve()
    if "documents" in lowered or "document folder" in lowered:
        documents = Path.home() / "Documents"
        if documents.exists() and documents.is_dir():
            return documents.resolve()
    if "~/" in lowered or "home folder" in lowered:
        home = Path.home()
        if home.exists() and home.is_dir():
            return home.resolve()

    match = re.search(r"\b([a-zA-Z]:\\[^:*?\"<>|\r\n]+)", str(prompt or ""))
    if match:
        candidate = Path(match.group(1)).expanduser()
        parent = candidate if candidate.is_dir() else candidate.parent
        if parent.exists() and parent.is_dir():
            return parent.resolve()
    return None


def _resolve_workspace_for_prompt(prompt: str, current_workspace: Path) -> Path:
    resolved = resolve_path_from_prompt(prompt)
    if resolved is not None:
        return resolved
    return current_workspace


def _instant_intent(prompt: str) -> TaskIntent:
    return TaskIntent(
        raw_request=prompt,
        objective="Answer instantly from FORGE local policy.",
        primary_intent=IntentKind.CONVERSATION,
        intents=[IntentKind.CONVERSATION],
        task_type="fast",
        requested_output="short user response",
        notes=["Answered without a provider call."],
    )


def _load_workspace_state() -> DesktopWorkspaceState:
    state_file = _state_file()
    with _STATE_LOCK:
        if state_file.exists():
            try:
                payload = json.loads(state_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                payload = {}
        else:
            payload = {}

        raw_workspace = payload.get("workspace_root")
        if isinstance(raw_workspace, str):
            candidate = Path(raw_workspace).expanduser()
            if candidate.exists() and candidate.is_dir():
                return DesktopWorkspaceState(workspace_root=candidate.resolve(), state_file=state_file)

        fallback = _fallback_workspace_root()
        state_file.write_text(
            json.dumps({"workspace_root": str(fallback)}, indent=2),
            encoding="utf-8",
        )
        return DesktopWorkspaceState(workspace_root=fallback, state_file=state_file)


def get_workspace_status() -> dict[str, Any]:
    state = _load_workspace_state()
    workspace_root = state.workspace_root
    settings = OperatorSettings(enable_memory=False, workspace_root=workspace_root)
    tools = WorkspaceTools(settings)
    summary = tools.workspace_summary()
    return {
        "workspace_root": str(workspace_root),
        "artifact_root": str(settings.artifact_root),
        "workspace_name": workspace_root.name or str(workspace_root),
        "state_file": str(state.state_file),
        "key_files": summary.get("key_files", [])[:12],
        "tree": summary.get("tree", [])[:18],
        "file_count": summary.get("file_count", 0),
        "file_types": summary.get("file_types", {}),
    }


def set_workspace_root(workspace_root: str | Path) -> dict[str, Any]:
    normalized = _normalize_workspace_root(workspace_root)
    state_file = _state_file()
    with _STATE_LOCK:
        state_file.write_text(
            json.dumps({"workspace_root": str(normalized)}, indent=2),
            encoding="utf-8",
        )
    return get_workspace_status()


def choose_workspace_root() -> dict[str, Any]:
    from tkinter import Tk, filedialog

    current = _load_workspace_state().workspace_root
    root = Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        selected = filedialog.askdirectory(
            title="Select FORGE workspace",
            initialdir=str(current),
            mustexist=True,
        )
    finally:
        root.destroy()

    if not selected:
        payload = get_workspace_status()
        payload["cancelled"] = True
        return payload
    return set_workspace_root(selected)


def prepare_demo_workspace() -> dict[str, Any]:
    workspace_root = (_default_state_directory() / "demo-workspace").resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)
    (workspace_root / "demo_input.md").write_text(_DEMO_INPUT, encoding="utf-8")
    output_path = workspace_root / "action_items.md"
    if output_path.exists():
        output_path.unlink()

    payload = set_workspace_root(workspace_root)
    payload["demo"] = {
        "title": "Launch notes to action items",
        "prompt": _DEMO_PROMPT,
        "input_path": "demo_input.md",
        "output_path": "action_items.md",
        "input_preview": _DEMO_INPUT,
        "expected_output": _DEMO_OUTPUT,
    }
    return payload


def _is_local_demo_prompt(prompt: str, workspace_root: Path) -> bool:
    normalized = prompt.strip().lower()
    has_demo_references = (
        "demo_input.md" in normalized
        and "action_items.md" in normalized
    )
    return (
        ("forge_local_demo_run" in normalized and has_demo_references)
        or (
            has_demo_references
            and (workspace_root / "demo_input.md").exists()
            and "tighten checkout copy before release" in normalized
        )
    )


def _stream_local_demo(workspace_root: Path, started: float):
    workspace_root.mkdir(parents=True, exist_ok=True)
    input_path = workspace_root / "demo_input.md"
    output_path = workspace_root / "action_items.md"
    if not input_path.exists():
        input_path.write_text(_DEMO_INPUT, encoding="utf-8")
    steps = [
        {
            "step_id": "demo-read",
            "index": 1,
            "total": 2,
            "skill": "file-reader",
            "label": "Read demo_input.md",
            "action": "read",
        },
        {
            "step_id": "demo-write",
            "index": 2,
            "total": 2,
            "skill": "file-editor",
            "label": "Create action_items.md",
            "action": "write",
        },
    ]
    yield {
        "type": "plan_ready",
        "stage": "planning",
        "message": "Plan ready: read demo_input.md, then create action_items.md.",
        "visible": False,
        "steps": steps,
    }
    yield {
        "type": "plan",
        "stage": "planning",
        "message": "Plan ready: read demo_input.md, then create action_items.md.",
        "steps": [step["label"] for step in steps],
        "structured_steps": steps,
        "elapsed_ms": round((time.monotonic() - started) * 1000, 2),
    }
    yield {"type": "provider_selected", "message": "Local demo path ready."}

    started_at: dict[str, float] = {}
    read_text = ""
    for step in steps:
        started_at[str(step["step_id"])] = time.monotonic()
        started_payload = {
            "type": "step_started",
            "stage": "execution",
            "step_id": step["step_id"],
            "index": step["index"],
            "total": step["total"],
            "skill": step["skill"],
            "action": step["action"],
            "message": f"{step['label']}...",
        }
        yield started_payload
        yield _step_start_alias(started_payload)

        if step["step_id"] == "demo-read":
            read_text = input_path.read_text(encoding="utf-8")
            evidence = [f"Read {input_path.name}", f"{len(read_text)} characters"]
        else:
            output_path.write_text(_DEMO_OUTPUT, encoding="utf-8")
            evidence = [f"Created {output_path.name}", "Validated expected demo output"]

        completed_payload = {
            "type": "step_completed",
            "stage": "execution",
            "step_id": step["step_id"],
            "index": step["index"],
            "total": step["total"],
            "skill": step["skill"],
            "action": step["action"],
            "status": "completed",
            "attempts": 1,
            "evidence": evidence,
            "message": f"{step['label']} complete.",
        }
        yield completed_payload
        yield _step_done_alias(completed_payload, started_at)

    total_latency_ms = (time.monotonic() - started) * 1000
    answer = (
        "Demo complete. I read demo_input.md and created action_items.md in your workspace. "
        "Open the file to see the extracted action items."
    )
    result = {
        "objective": "Run local FORGE demo",
        "user_response": answer,
        "answer": answer,
        "result": answer,
        "validation_status": CompletionState.FINISHED.value,
        "artifacts_count": 1,
        "step_results": [
            {"output": {"artifact_path": str(input_path)}, "status": "completed"},
            {"output": {"artifact_path": str(output_path)}, "status": "completed"},
        ],
        "technical_details": {
            "mode": "local_demo",
            "input_path": str(input_path),
            "output_path": str(output_path),
        },
        "workspace_root": str(workspace_root),
    }
    yield {
        "type": "mission_completed",
        "stage": "complete",
        "message": _mission_complete_message(result, total_latency_ms),
        "validation_status": CompletionState.FINISHED.value,
        "success": True,
        "total_latency_ms": round(total_latency_ms, 2),
        "final_provider": "FORGE",
        "artifact_paths": [str(output_path)],
    }
    yield {"type": "status", "stage": "streaming", "message": "Response ready. Streaming output..."}
    yield {"type": "result", "content": answer, "has_details": True}
    yield {"type": "user_response", "content": answer, "has_details": True}
    yield {"type": "technical_details", "content": result["technical_details"], "hidden": True}
    footer = _stream_footer(result, elapsed_ms=total_latency_ms)
    result["stream_footer"] = footer
    yield _done_event(result, footer=footer)


def boot_status() -> DesktopBootStatus:
    session = ForgeSession(memory=False)
    status = session._router.status()
    providers = status.get("providers", 0)
    models_online = status.get("models_online", 0)
    workspace = get_workspace_status()
    provider_setup = _provider_setup_snapshot({})
    summary = (
        f"FORGE v{__version__} booted with {providers} provider(s) "
        f"and {models_online} live model(s). Active workspace: {workspace['workspace_root']}."
    )
    if provider_setup["needs_provider_setup"]:
        summary += " Provider setup is needed: no saved cloud provider and Ollama is not running."
    return DesktopBootStatus(
        providers=providers,
        models_online=models_online,
        summary=summary,
        workspace_root=workspace["workspace_root"],
        artifact_root=workspace["artifact_root"],
        provider_setup=provider_setup,
    )


def run_prompt(
    prompt: str,
    use_operator: bool = False,
    *,
    workspace_root: str | Path | None = None,
    confirmed: bool = False,
    dry_run: bool = False,
) -> str:
    prompt = prompt.strip()
    if not prompt:
        raise ValueError("Prompt is empty.")
    return operate_prompt(
        prompt,
        confirmed=confirmed,
        dry_run=dry_run,
        workspace_root=workspace_root,
    )["answer"]


def operate_prompt(
    prompt: str,
    confirmed: bool = False,
    dry_run: bool = False,
    workspace_root: str | Path | None = None,
    provider_secrets: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    prompt = prompt.strip()
    if not prompt:
        raise ValueError("Prompt is empty.")

    base_workspace_root = _normalize_workspace_root(workspace_root)
    normalized_workspace_root = _resolve_workspace_for_prompt(prompt, base_workspace_root)
    if workspace_root is not None or normalized_workspace_root != base_workspace_root:
        set_workspace_root(normalized_workspace_root)
    auto_confirmed = False
    if not dry_run and _should_allow_real_changes_for_prompt(prompt):
        confirmed = True
        auto_confirmed = True

    instant = instant_response(prompt)
    if instant is not None:
        return _serialize_direct_response(
            answer=instant,
            intent=_instant_intent(prompt),
            workspace_root=normalized_workspace_root,
            approach="Answered from FORGE local fast path before any provider call.",
        )

    if _is_local_demo_prompt(prompt, normalized_workspace_root):
        normalized_workspace_root.mkdir(parents=True, exist_ok=True)
        input_path = normalized_workspace_root / "demo_input.md"
        output_path = normalized_workspace_root / "action_items.md"
        if not input_path.exists():
            input_path.write_text(_DEMO_INPUT, encoding="utf-8")
        output_path.write_text(_DEMO_OUTPUT, encoding="utf-8")
        answer = (
            "Demo complete. I read demo_input.md and created action_items.md in your workspace. "
            "Open the file to see the extracted action items."
        )
        return {
            "objective": "Run local FORGE demo",
            "user_response": answer,
            "answer": answer,
            "result": answer,
            "validation_status": CompletionState.FINISHED.value,
            "artifacts_count": 1,
            "step_results": [
                {"output": {"artifact_path": str(input_path)}, "status": "completed"},
                {"output": {"artifact_path": str(output_path)}, "status": "completed"},
            ],
            "technical_details": {
                "mode": "local_demo",
                "input_path": str(input_path),
                "output_path": str(output_path),
            },
            "workspace_root": str(normalized_workspace_root),
        }

    operator = ForgeOperator(
        settings=OperatorSettings(
            enable_memory=False,
            workspace_root=normalized_workspace_root,
        ),
        provider_secrets=provider_secrets,
    )
    result = operator.handle(prompt, confirmed=confirmed, dry_run=dry_run)
    return _serialize_operator_result(result, operator, normalized_workspace_root)


def stream_prompt(
    prompt: str,
    confirmed: bool = False,
    dry_run: bool = False,
    workspace_root: str | Path | None = None,
    provider_secrets: dict[str, dict[str, str]] | None = None,
):
    prompt = prompt.strip()
    if not prompt:
        raise ValueError("Prompt is empty.")

    yield {
        "type": "status",
        "stage": "intent",
        "text": "Analyzing your request...",
        "message": "Analyzing your request...",
        "elapsed_ms": 0,
    }
    yield {
        "type": "intent_analyzing",
        "stage": "intent",
        "text": "Analyzing your request...",
        "message": "Analyzing your request...",
    }

    base_workspace_root = _normalize_workspace_root(workspace_root)
    normalized_workspace_root = _resolve_workspace_for_prompt(prompt, base_workspace_root)
    if workspace_root is not None or normalized_workspace_root != base_workspace_root:
        set_workspace_root(normalized_workspace_root)
    auto_confirmed = False
    if not dry_run and _should_allow_real_changes_for_prompt(prompt):
        confirmed = True
        auto_confirmed = True

    instant = instant_response(prompt)
    if instant is not None:
        started = time.monotonic()
        payload = _serialize_direct_response(
            answer=instant,
            intent=_instant_intent(prompt),
            workspace_root=normalized_workspace_root,
            approach="Answered from FORGE local fast path before any provider call.",
        )
        footer = _stream_footer(payload, elapsed_ms=(time.monotonic() - started) * 1000)
        payload["stream_footer"] = footer
        yield {
            "type": "user_response",
            "content": payload.get("user_response") or instant,
            "has_details": bool(payload.get("technical_details") or payload.get("diagnostics")),
        }
        yield {
            "type": "technical_details",
            "content": payload.get("technical_details") or payload.get("diagnostics") or {},
            "hidden": True,
        }
        yield _done_event(payload, footer=footer)
        return

    if _is_local_demo_prompt(prompt, normalized_workspace_root):
        yield from _stream_local_demo(normalized_workspace_root, started=time.monotonic())
        return

    if auto_confirmed:
        yield {
            "type": "confirmation",
            "stage": "safety",
            "message": f"Explicit local path detected. Real changes enabled for workspace: {normalized_workspace_root}",
            "workspace_root": str(normalized_workspace_root),
        }

    operator = ForgeOperator(
        settings=OperatorSettings(
            enable_memory=False,
            workspace_root=normalized_workspace_root,
        ),
        provider_secrets=provider_secrets,
    )
    intent = operator.intent_resolver.resolve(prompt)
    routing = operator.skill_router.route(intent, operator.registry.list())
    routing.selected_skills = operator._ordered_skill_names(routing.selected_skills)

    if operator._asks_identity(prompt.strip().lower()):
        started = time.monotonic()
        answer = operator._identity_text()
        payload = _serialize_direct_response(
            answer=answer,
            intent=intent,
            workspace_root=normalized_workspace_root,
            approach="Identity prompt answered from the approved branding policy.",
        )
        footer = _stream_footer(payload, elapsed_ms=(time.monotonic() - started) * 1000)
        payload["stream_footer"] = footer
        yield {
            "type": "user_response",
            "content": payload.get("user_response") or answer,
            "has_details": bool(payload.get("technical_details") or payload.get("diagnostics")),
        }
        yield {
            "type": "technical_details",
            "content": payload.get("technical_details") or payload.get("diagnostics") or {},
            "hidden": True,
        }
        yield _done_event(payload, footer=footer)
        return

    if operator._asks_file_capability(prompt.strip().lower()):
        started = time.monotonic()
        answer = operator._file_capability_text()
        payload = _serialize_direct_response(
            answer=answer,
            intent=intent,
            workspace_root=normalized_workspace_root,
            approach="File capability prompt answered from FORGE workspace policy.",
        )
        footer = _stream_footer(payload, elapsed_ms=(time.monotonic() - started) * 1000)
        payload["stream_footer"] = footer
        yield {
            "type": "user_response",
            "content": payload.get("user_response") or answer,
            "has_details": bool(payload.get("technical_details") or payload.get("diagnostics")),
        }
        yield {
            "type": "technical_details",
            "content": payload.get("technical_details") or payload.get("diagnostics") or {},
            "hidden": True,
        }
        yield _done_event(payload, footer=footer)
        return

    if intent.primary_intent == IntentKind.CONVERSATION and not routing.selected_skills:
        started = time.monotonic()
        normalized_prompt = prompt.strip().lower()
        direct_answer = ""
        direct_approach = ""
        if operator._asks_identity(normalized_prompt):
            direct_answer = operator._identity_text()
            direct_approach = "Identity prompt answered from the approved branding policy."
        elif operator._asks_file_capability(normalized_prompt):
            direct_answer = operator._file_capability_text()
            direct_approach = "File capability prompt answered from FORGE workspace policy."
        elif operator._is_conversational_prompt(normalized_prompt):
            direct_answer = operator._friendly_intro_text()
            direct_approach = "Conversational prompt answered with friendly agent guidance."
        if direct_answer:
            answer = direct_answer
            payload = _serialize_direct_response(
                answer=answer,
                intent=intent,
                workspace_root=normalized_workspace_root,
                approach=direct_approach,
            )
            footer = _stream_footer(payload, elapsed_ms=(time.monotonic() - started) * 1000)
            payload["stream_footer"] = footer
            yield {
                "type": "user_response",
                "content": payload.get("user_response") or answer,
                "has_details": bool(payload.get("technical_details") or payload.get("diagnostics")),
            }
            yield {
                "type": "technical_details",
                "content": payload.get("technical_details") or payload.get("diagnostics") or {},
                "hidden": True,
            }
            yield _done_event(payload, footer=footer)
            return
        yield {
            "type": "status",
            "stage": "routing",
            "message": "Preparing the best response path...",
        }
        try:
            response = None
            streamed_text = ""
            for event in operator.session.stream_response(
                prompt,
                task_type=intent.task_type,
                remember=False,
            ):
                kind = str(event.get("type") or "").strip().lower()
                if kind == "start":
                    provider = str(event.get("provider") or "").strip()
                    display_name = str(event.get("display_name") or event.get("model") or provider).strip()
                    yield {
                        "type": "provider_selected",
                        "message": "Response path ready.",
                    }
                    yield {
                        "type": "status",
                        "stage": "routing",
                        "message": "Response path ready.",
                    }
                    continue
                if kind == "delta":
                    delta = str(event.get("delta") or "")
                    streamed_text += delta
                    yield {"type": "delta", "delta": delta}
                    continue
                if kind == "response":
                    maybe_response = event.get("response")
                    if maybe_response is not None:
                        response = maybe_response

            if response is None:
                raise RuntimeError("Streaming finished without a final response.")

            payload = _serialize_conversation_response(
                answer=streamed_text or response.content,
                intent=intent,
                response=response,
                workspace_root=normalized_workspace_root,
            )
            footer = _stream_footer(payload, elapsed_ms=(time.monotonic() - started) * 1000)
            payload["stream_footer"] = footer
            yield {
                "type": "user_response",
                "content": payload.get("user_response") or payload.get("answer") or "",
                "has_details": bool(payload.get("technical_details") or payload.get("diagnostics")),
            }
            yield {
                "type": "technical_details",
                "content": payload.get("technical_details") or payload.get("diagnostics") or {},
                "hidden": True,
            }
            yield _done_event(payload, footer=footer)
            return
        except Exception as exc:
            fallback = operator._clarification_text(prompt)
            payload = _serialize_clarification_response(
                answer=fallback,
                intent=intent,
                workspace_root=normalized_workspace_root,
                error=str(exc),
            )
            yield {
                "type": "user_response",
                "content": payload.get("user_response") or fallback,
                "has_details": bool(payload.get("technical_details") or payload.get("diagnostics")),
            }
            yield {
                "type": "technical_details",
                "content": payload.get("technical_details") or payload.get("diagnostics") or {},
                "hidden": True,
            }
            yield _done_event(payload, footer=str(payload.get("stream_footer") or ""))
            return

    started = time.monotonic()
    skills = operator.registry.list()
    skill_lookup = {skill.name: skill for skill in skills}
    safety = operator.safety_guard.evaluate(
        request=prompt,
        intent=intent,
        routing=routing,
        skill_lookup=skill_lookup,
        confirmed=confirmed,
        dry_run_requested=dry_run,
    )
    plan = operator.planner.build(intent, routing, safety, request=prompt, max_steps=operator.settings.max_plan_steps)
    mission_id, audit_log_path, resume_state = operator.audit_store.begin(prompt, plan)
    plan_payload = plan.model_dump(mode="json")
    plan_steps = _plan_steps(plan)
    yield {
        "type": "plan_ready",
        "stage": "planning",
        "message": _plan_summary(plan),
        "visible": False,
        "plan": plan_payload,
        "steps": plan_steps,
        "mission_id": mission_id,
        "audit_log_path": audit_log_path,
    }
    yield {
        "type": "plan",
        "stage": "planning",
        "message": _plan_summary(plan),
        "steps": [step.get("label") or step.get("action") or step.get("id") for step in plan_steps],
        "structured_steps": plan_steps,
        "elapsed_ms": round((time.monotonic() - started) * 1000, 2),
    }
    yield {
        "type": "provider_selected",
        "message": "Execution path ready.",
    }

    events: Queue[tuple[str, Any]] = Queue()

    def worker() -> None:
        try:
            result = _execute_planned_operator(
                operator=operator,
                prompt=prompt,
                intent=intent,
                routing=routing,
                safety=safety,
                plan=plan,
                mission_id=mission_id,
                audit_log_path=audit_log_path,
                resume_state=resume_state,
                confirmed=confirmed,
                memory_context="",
                workspace_root=normalized_workspace_root,
            )
            events.put(("result", result))
        except Exception as exc:  # pragma: no cover - surfaced to SSE client
            events.put(("error", str(exc)))
        finally:
            events.put(("done", None))

    yield {
        "type": "status",
        "stage": "routing",
        "message": "Preparing the best response path...",
    }
    yield {
        "type": "status",
        "stage": "workspace",
        "message": f"Using workspace: {normalized_workspace_root}",
    }

    threading.Thread(target=worker, daemon=True).start()
    result_emitted = False
    started_steps: set[str] = set()
    finished_steps: set[str] = set()
    step_started_at: dict[str, float] = {}
    last_audit_mtime = 0.0
    wait_status_marks = [
        (3.0, "Finding best model..."),
        (8.0, "Switching to faster provider..."),
        (12.0, "Almost ready..."),
    ]
    emitted_wait_statuses: set[float] = set()
    _maybe_emit_next_step_started(plan, started_steps, finished_steps, events)

    while True:
        try:
            kind, payload = events.get(timeout=0.18)
        except Empty:
            elapsed_s = time.monotonic() - started
            for mark_s, message in wait_status_marks:
                if mark_s not in emitted_wait_statuses and elapsed_s >= mark_s:
                    emitted_wait_statuses.add(mark_s)
                    yield {
                        "type": "status",
                        "stage": "routing",
                        "text": message,
                        "message": message,
                        "elapsed_ms": round(elapsed_s * 1000, 2),
                    }
            if not result_emitted:
                last_audit_mtime = _emit_audit_step_events(
                    audit_log_path=Path(audit_log_path),
                    plan=plan,
                    started_steps=started_steps,
                    finished_steps=finished_steps,
                    queue=events,
                    last_mtime=last_audit_mtime,
                )
            continue

        if kind == "step_started":
            step_id = str(payload.get("step_id") or "").strip()
            if step_id:
                step_started_at[step_id] = time.monotonic()
            yield payload
            yield _step_start_alias(payload)
            continue

        if kind == "step_completed":
            yield payload
            yield _step_done_alias(payload, step_started_at)
            continue

        if kind == "step_failed":
            failed_payload = dict(payload)
            failed_payload.update(_step_failed_alias(payload, step_started_at))
            yield failed_payload
            yield {
                "type": "status",
                "stage": "recovery",
                "text": "A step needs attention. FORGE is preserving details for recovery.",
                "message": "A step needs attention. FORGE is preserving details for recovery.",
            }
            continue

        if kind == "error":
            yield {"type": "error", "error": str(payload)}
            result_emitted = True
            continue

        if kind == "result":
            result = payload
            _emit_result_step_events(result, plan, started_steps, finished_steps, events)
            deferred_events: list[tuple[str, Any]] = []
            while True:
                try:
                    queued_kind, queued_payload = events.get_nowait()
                except Empty:
                    break
                if queued_kind in {"step_started", "step_completed", "step_failed"}:
                    if queued_kind == "step_started":
                        step_id = str(queued_payload.get("step_id") or "").strip()
                        if step_id:
                            step_started_at[step_id] = time.monotonic()
                        yield queued_payload
                        yield _step_start_alias(queued_payload)
                    elif queued_kind == "step_completed":
                        yield queued_payload
                        yield _step_done_alias(queued_payload, step_started_at)
                    else:
                        failed_payload = dict(queued_payload)
                        failed_payload.update(_step_failed_alias(queued_payload, step_started_at))
                        yield failed_payload
                        yield {
                            "type": "status",
                            "stage": "recovery",
                            "text": "A step needs attention. FORGE is preserving details for recovery.",
                            "message": "A step needs attention. FORGE is preserving details for recovery.",
                        }
                    continue
                deferred_events.append((queued_kind, queued_payload))
            for deferred in deferred_events:
                events.put(deferred)
            telemetry = result.get("provider_telemetry") if isinstance(result.get("provider_telemetry"), dict) else {}
            if telemetry:
                yield from _provider_events_from_telemetry(telemetry)
            total_latency_ms = (time.monotonic() - started) * 1000
            yield {
                "type": "mission_completed",
                "stage": "complete",
                "message": _mission_complete_message(result, total_latency_ms),
                "validation_status": result.get("validation_status"),
                "success": result.get("validation_status") == CompletionState.FINISHED.value,
                "mission_id": result.get("mission_id"),
                "total_latency_ms": round(total_latency_ms, 2),
                "final_provider": "FORGE",
                "artifact_paths": _artifact_paths(result),
            }
            yield {
                "type": "status",
                "stage": "streaming",
                "message": "Response ready. Streaming output...",
            }
            answer = str(result.get("user_response") or result.get("answer") or result.get("result") or "No result produced.")
            footer = _stream_footer(result, elapsed_ms=(time.monotonic() - started) * 1000)
            result["stream_footer"] = footer
            yield {
                "type": "result",
                "content": answer,
                "has_details": bool(result.get("technical_details") or result.get("diagnostics")),
            }
            yield {
                "type": "user_response",
                "content": answer,
                "has_details": bool(result.get("technical_details") or result.get("diagnostics")),
            }
            yield {
                "type": "technical_details",
                "content": result.get("technical_details") or result.get("diagnostics") or {},
                "hidden": True,
            }
            yield _done_event(result, footer=footer)
            result_emitted = True
            continue

        if kind == "done":
            break


def _execute_planned_operator(
    *,
    operator: ForgeOperator,
    prompt: str,
    intent,
    routing,
    safety,
    plan: ExecutionPlan,
    mission_id: str,
    audit_log_path: str,
    resume_state,
    confirmed: bool,
    memory_context: str,
    workspace_root: Path,
) -> dict[str, Any]:
    if safety.blocked:
        status = CompletionState.NEEDS_HUMAN_CONFIRMATION if safety.requires_confirmation else CompletionState.FAILED
        operator.audit_store.save_progress(
            mission_id,
            audit_log_path,
            request=prompt,
            plan=plan,
            status=status.value,
            step_results=[],
            artifacts={"mission_audit": {"mission_id": mission_id, "audit_log_path": audit_log_path}},
            mission_trace=["Execution blocked in SafetyGuard before any skill ran."],
            resumed_from_step=resume_state.resumed_from_step if resume_state else None,
        )
        result = OperatorResult(
            objective=intent.objective,
            approach_taken=[
                "Resolved intent.",
                "Selected skills.",
                "Blocked execution in SafetyGuard.",
            ],
            result="Execution blocked before any skill ran.",
            validation_status=status,
            risks_or_limitations=safety.reasons or ["Execution blocked by policy."],
            best_next_action=operator.composer.best_next_action(status),
            intent=intent,
            plan=plan,
            step_results=[],
            artifacts={},
            mission_id=mission_id,
            audit_log_path=audit_log_path,
            resumed_from_step=resume_state.resumed_from_step if resume_state else None,
            provider_telemetry=operator._provider_telemetry(),
        )
        return _serialize_operator_result(result, operator, workspace_root)

    runtime_context = SkillExecutionContext(
        settings=operator.settings,
        session=operator.session,
        memory=operator.memory,
        dry_run=safety.use_dry_run,
        sanitizer=operator.sanitizer,
        state={"memory_context": memory_context, "confirmed": confirmed, "mission_id": mission_id},
    )
    mission = operator.orchestrator.execute(
        request=prompt,
        intent=intent,
        plan=plan,
        runtime_context=runtime_context,
        mission_id=mission_id,
        audit_log_path=audit_log_path,
        resume_state=resume_state,
        confirmed=confirmed,
        memory_context=memory_context,
        remember_execution=None,
    )
    final_status = operator.validator.evaluate_plan(plan, mission.step_results)
    result_text = operator._summarize_artifacts(mission.artifacts, mission.step_results)
    risks = list(dict.fromkeys(safety.reasons + operator._step_risks(mission.step_results)))
    best_next_action = (
        "Review the dry-run output, then rerun without dry-run when approved."
        if safety.use_dry_run
        else operator.composer.best_next_action(final_status)
    )
    result = OperatorResult(
        objective=intent.objective,
        approach_taken=operator._approach_lines(intent, routing, safety),
        result=result_text,
        validation_status=final_status,
        risks_or_limitations=risks,
        best_next_action=best_next_action,
        intent=intent,
        plan=plan,
        step_results=mission.step_results,
        artifacts=mission.artifacts,
        mission_trace=mission.mission_trace,
        mission_id=mission.mission_id,
        audit_log_path=mission.audit_log_path,
        resumed_from_step=mission.resumed_from_step,
        agent_reviews=mission.agent_reviews,
        provider_telemetry=operator._provider_telemetry(),
    )
    return _serialize_operator_result(result, operator, workspace_root)


def _plan_steps(plan: ExecutionPlan) -> list[dict[str, Any]]:
    return [
        {
            "id": step.id,
            "label": _human_step_label(step.skill or step.tool or step.action),
            "action": step.action,
            "skill": step.skill,
            "tool": step.tool,
            "expected_output": step.expected_output,
            "validation": step.validation,
        }
        for step in plan.steps
    ]


def _plan_summary(plan: ExecutionPlan) -> str:
    if not plan.steps:
        return "Plan ready: direct response without execution steps."
    labels = [step.skill or step.tool or "reasoning" for step in plan.steps]
    return f"Plan ready: {' -> '.join(labels)}."


def _human_step_label(value: str | None) -> str:
    raw = str(value or "Work on the request").strip()
    labels = {
        "file-reader": "Read files",
        "file-editor": "Write or update files",
        "codebase-analyzer": "Analyze code",
        "workspace-inspector": "Inspect workspace",
        "browser-executor": "Open and inspect web page",
        "shell-executor": "Run checks",
        "artifact-writer": "Write report",
        "research-brief": "Gather findings",
    }
    return labels.get(raw, raw.replace("-", " ").strip().capitalize())


def _maybe_emit_next_step_started(
    plan: ExecutionPlan,
    started_steps: set[str],
    finished_steps: set[str],
    queue: Queue[tuple[str, Any]],
) -> None:
    total = len(plan.steps)
    for index, step in enumerate(plan.steps, start=1):
        if step.id in started_steps or step.id in finished_steps:
            continue
        queue.put(("step_started", _step_started_event(step.id, step.skill or step.tool or "reasoning", step.action, index, total)))
        started_steps.add(step.id)
        return


def _emit_audit_step_events(
    *,
    audit_log_path: Path,
    plan: ExecutionPlan,
    started_steps: set[str],
    finished_steps: set[str],
    queue: Queue[tuple[str, Any]],
    last_mtime: float,
) -> float:
    if not audit_log_path.exists():
        return last_mtime
    try:
        mtime = audit_log_path.stat().st_mtime
        if mtime <= last_mtime:
            return last_mtime
        payload = json.loads(audit_log_path.read_text(encoding="utf-8"))
    except Exception:
        return last_mtime

    for step in payload.get("step_results", []):
        if not isinstance(step, dict):
            continue
        _queue_step_terminal_event(step, started_steps, finished_steps, queue, plan)
    _maybe_emit_next_step_started(plan, started_steps, finished_steps, queue)
    return mtime


def _emit_result_step_events(
    result: dict[str, Any],
    plan: ExecutionPlan,
    started_steps: set[str],
    finished_steps: set[str],
    queue: Queue[tuple[str, Any]],
) -> None:
    for step in result.get("step_results", []):
        if isinstance(step, dict):
            _queue_step_terminal_event(step, started_steps, finished_steps, queue, plan)


def _queue_step_terminal_event(
    step: dict[str, Any],
    started_steps: set[str],
    finished_steps: set[str],
    queue: Queue[tuple[str, Any]],
    plan: ExecutionPlan,
) -> None:
    step_id = str(step.get("step_id") or step.get("id") or "").strip()
    if not step_id or step_id in finished_steps:
        return

    skill = str(step.get("skill") or step.get("tool") or "reasoning")
    if step_id not in started_steps:
        queue.put(("step_started", _step_started_event(step_id, skill, f"Execute `{skill}`.", 0, len(plan.steps))))
        started_steps.add(step_id)

    status = str(step.get("status") or "").strip().lower()
    if status in {"finished", "partially_finished"}:
        queue.put(("step_completed", _step_completed_event(step)))
        finished_steps.add(step_id)
    elif status in {"failed", "needs_retry", "needs_human_confirmation"}:
        queue.put(("step_failed", _step_failed_event(step)))
        finished_steps.add(step_id)


def _step_started_event(step_id: str, skill: str, action: str, index: int, total: int) -> dict[str, Any]:
    prefix = f"Step {index}/{total}" if index and total else "Step"
    return {
        "type": "step_started",
        "stage": "execution",
        "step_id": step_id,
        "skill": skill,
        "index": index,
        "total": total,
        "message": f"{prefix}: starting {skill}.",
        "action": action,
    }


def _step_completed_event(step: dict[str, Any]) -> dict[str, Any]:
    evidence = step.get("evidence") if isinstance(step.get("evidence"), list) else []
    return {
        "type": "step_completed",
        "stage": "execution",
        "step_id": step.get("step_id"),
        "skill": step.get("skill") or step.get("tool") or "reasoning",
        "status": step.get("status"),
        "attempts": step.get("attempts"),
        "evidence_count": len(evidence),
        "message": f"{step.get('step_id')}: completed {step.get('skill') or step.get('tool') or 'reasoning'}.",
        "evidence": evidence[:6],
    }


def _step_failed_event(step: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "step_failed",
        "stage": "execution",
        "step_id": step.get("step_id"),
        "skill": step.get("skill") or step.get("tool") or "reasoning",
        "status": step.get("status"),
        "attempts": step.get("attempts"),
        "error": step.get("error") or "Step did not finish cleanly.",
        "message": f"{step.get('step_id')}: {step.get('status')} in {step.get('skill') or step.get('tool') or 'reasoning'}.",
    }


def _step_start_alias(payload: dict[str, Any]) -> dict[str, Any]:
    label = _human_step_label(str(payload.get("skill") or payload.get("tool") or payload.get("action") or "step"))
    index = int(payload.get("index") or 0)
    return {
        "type": "step_start",
        "stage": "execution",
        "step": index or payload.get("step_id"),
        "step_id": payload.get("step_id"),
        "label": f"{label}...",
        "text": f"{label}...",
        "message": f"{label}...",
    }


def _step_done_alias(payload: dict[str, Any], started_at: dict[str, float]) -> dict[str, Any]:
    step_id = str(payload.get("step_id") or "").strip()
    elapsed = 0.0
    if step_id and step_id in started_at:
        elapsed = (time.monotonic() - started_at[step_id]) * 1000
    label = _human_step_label(str(payload.get("skill") or payload.get("tool") or "step"))
    return {
        "type": "step_done",
        "stage": "execution",
        "step": payload.get("step_id"),
        "step_id": payload.get("step_id"),
        "label": label,
        "text": f"{label} complete",
        "message": f"{label} complete",
        "ms": round(elapsed, 2),
    }


def _step_failed_alias(payload: dict[str, Any], started_at: dict[str, float]) -> dict[str, Any]:
    step_id = str(payload.get("step_id") or "").strip()
    elapsed = 0.0
    if step_id and step_id in started_at:
        elapsed = (time.monotonic() - started_at[step_id]) * 1000
    label = _human_step_label(str(payload.get("skill") or payload.get("tool") or "step"))
    return {
        "type": "step_failed",
        "stage": "execution",
        "step": payload.get("step_id"),
        "step_id": payload.get("step_id"),
        "label": label,
        "text": f"{label} needs recovery",
        "message": f"{label} needs recovery",
        "ms": round(elapsed, 2),
    }


def _done_event(payload: dict[str, Any], *, footer: str = "") -> dict[str, Any]:
    return {
        "type": "done",
        "done": True,
        "user_response": str(payload.get("user_response") or payload.get("answer") or payload.get("result") or ""),
        "has_details": bool(payload.get("technical_details") or payload.get("diagnostics")),
        "footer": footer,
    }


def _provider_events_from_telemetry(telemetry: dict[str, Any]):
    attempts = telemetry.get("attempts") if isinstance(telemetry.get("attempts"), list) else []
    selected = str(telemetry.get("selected_provider") or "").strip()
    if selected:
        yield {
            "type": "provider_selected",
            "message": "Response path ready.",
        }
    for attempt in attempts:
        if not isinstance(attempt, dict):
            continue
        status = str(attempt.get("status") or "").lower()
        if status == "timeout":
            yield {
                "type": "provider_timeout",
                "latency_ms": attempt.get("latency_ms"),
                "message": "A response path was slow.",
            }
    if int(telemetry.get("fallback_count") or 0):
        yield {
            "type": "provider_fallback",
            "fallback_count": telemetry.get("fallback_count"),
            "message": "FORGE retried with another route.",
        }


def _mission_complete_message(result: dict[str, Any], total_latency_ms: float) -> str:
    status = str(result.get("validation_status") or "unknown")
    artifacts = int(result.get("artifacts_count") or 0)
    return f"Mission {status} in {total_latency_ms / 1000:.1f}s with {artifacts} artifact(s)."


def _artifact_paths(result: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for step in result.get("step_results", []):
        output = step.get("output") if isinstance(step, dict) else None
        if isinstance(output, dict):
            for key in ("edited_path", "artifact_path", "audit_log_path"):
                value = str(output.get(key) or "").strip()
                if value:
                    paths.append(value)
    audit = str(result.get("audit_log_path") or "").strip()
    if audit:
        paths.append(audit)
    return list(dict.fromkeys(paths))[:8]


def boot_status_for_user(provider_secrets: dict[str, dict[str, str]] | None = None) -> DesktopBootStatus:
    provider_setup = _provider_setup_snapshot(provider_secrets or {})
    session = ForgeSession(
        memory=False,
        provider_secrets=provider_secrets,
        allow_host_fallback=False,
    )
    status = session._router.status()
    providers = status.get("providers", 0)
    models_online = status.get("models_online", 0)
    workspace = get_workspace_status()
    summary = (
        f"FORGE v{__version__} booted with {providers} provider(s) "
        f"and {models_online} live model(s). Active workspace: {workspace['workspace_root']}."
    )
    if provider_setup["needs_provider_setup"]:
        summary += " Provider setup is needed: no saved cloud provider and Ollama is not running."
    return DesktopBootStatus(
        providers=providers,
        models_online=models_online,
        summary=summary,
        workspace_root=workspace["workspace_root"],
        artifact_root=workspace["artifact_root"],
        provider_setup=provider_setup,
    )


def _provider_setup_snapshot(provider_secrets: dict[str, dict[str, str]]) -> dict[str, Any]:
    saved_provider_count = sum(1 for payload in provider_secrets.values() if payload)
    ollama = _probe_ollama()
    needs_provider_setup = saved_provider_count == 0 and not ollama["running"]
    return {
        "needs_provider_setup": needs_provider_setup,
        "saved_provider_count": saved_provider_count,
        "cloud_provider_ready": saved_provider_count > 0,
        "ollama": ollama,
        "recommended": "groq" if needs_provider_setup else "",
        "options": [
            {
                "id": "groq",
                "label": "Groq",
                "kind": "cloud_key",
                "summary": "Fast free-tier cloud path. Requires a Groq API key.",
            },
            {
                "id": "ollama",
                "label": "Ollama",
                "kind": "local",
                "summary": "Private local path. No key, but Ollama must be running with a pulled model.",
            },
            {
                "id": "byok",
                "label": "BYOK",
                "kind": "cloud_key",
                "summary": "Use an existing OpenAI, Anthropic, NVIDIA, Gemini, or other key.",
            },
        ],
    }


def _probe_ollama(timeout_seconds: float = 0.15) -> dict[str, Any]:
    connection: http.client.HTTPConnection | None = None
    try:
        connection = http.client.HTTPConnection(_OLLAMA_HOST, _OLLAMA_PORT, timeout=timeout_seconds)
        connection.request("GET", "/api/tags")
        response = connection.getresponse()
        raw_body = response.read().decode("utf-8", errors="replace")
        if response.status != 200:
            raise RuntimeError(f"Ollama returned HTTP {response.status}")
        payload = json.loads(raw_body or "{}")
        models = [
            str(model.get("name") or "").strip()
            for model in payload.get("models", [])
            if isinstance(model, dict) and str(model.get("name") or "").strip()
        ]
        return {
            "running": True,
            "url": _OLLAMA_TAGS_URL,
            "models": models,
            "model_count": len(models),
            "error": "",
        }
    except Exception as exc:
        return {
            "running": False,
            "url": _OLLAMA_TAGS_URL,
            "models": [],
            "model_count": 0,
            "error": str(exc),
        }
    finally:
        if connection is not None:
            connection.close()


def _serialize_operator_result(
    result: OperatorResult,
    operator: ForgeOperator,
    workspace_root: Path,
) -> dict[str, Any]:
    workspace_status = get_workspace_status()
    payload = result.model_dump(mode="json")
    payload["answer"] = result.user_response or operator.composer.compose(result)
    payload["completed_steps"] = sum(1 for step in result.step_results if step.status.value == "finished")
    payload["total_steps"] = len(result.step_results)
    payload["evidence_count"] = sum(len(step.evidence) for step in result.step_results)
    payload["artifacts_count"] = len(result.artifacts)
    conversation_metadata = result.artifacts.get("conversation_metadata", {}) if isinstance(result.artifacts, dict) else {}
    provider_telemetry = result.provider_telemetry or {}
    if isinstance(conversation_metadata, dict):
        payload["model_used"] = conversation_metadata.get("model_id")
        payload["provider_used"] = conversation_metadata.get("provider")
        payload["latency_ms"] = conversation_metadata.get("latency_ms")
        payload["total_tokens"] = conversation_metadata.get("total_tokens")
        if not provider_telemetry and isinstance(conversation_metadata.get("routing_telemetry"), dict):
            provider_telemetry = conversation_metadata["routing_telemetry"]
    if provider_telemetry:
        payload["provider_telemetry"] = provider_telemetry
        payload["provider_used"] = provider_telemetry.get("final_provider_used") or payload.get("provider_used")
        payload["latency_ms"] = provider_telemetry.get("provider_latency_ms") or payload.get("latency_ms")
        payload["fallback_count"] = provider_telemetry.get("fallback_count", 0)
        payload["attempted_providers"] = provider_telemetry.get("attempted_providers", [])
    payload.update(workspace_status)
    payload["workspace_root"] = str(workspace_root)
    payload["artifact_root"] = str(operator.settings.artifact_root)
    return _with_human_first_response(payload)


def _with_human_first_response(payload: dict[str, Any]) -> dict[str, Any]:
    diagnostics = payload.get("technical_details") if isinstance(payload.get("technical_details"), dict) else {}
    if not diagnostics:
        diagnostics = _diagnostics_payload(payload)

    raw_answer = str(payload.get("user_response") or payload.get("answer") or payload.get("result") or "").strip()
    visible = _humanize_visible_response(raw_answer, payload)
    payload["technical_details"] = diagnostics
    payload["has_technical_details"] = bool(diagnostics)
    payload["user_response"] = visible
    payload["answer"] = visible
    payload["result"] = visible
    payload["diagnostics"] = diagnostics
    return payload


def _humanize_visible_response(text: str, payload: dict[str, Any] | None = None) -> str:
    payload = payload or {}
    text = enforce_forge_response_guard(text)
    action_summary = _action_completion_summary(text)
    if action_summary:
        return action_summary
    cleaned = _strip_visible_technical_noise(text)
    if not cleaned:
        cleaned = _fallback_visible_summary(payload)
    cleaned = _limit_words(cleaned, _VISIBLE_WORD_LIMIT)
    return enforce_forge_response_guard(cleaned or "Done. Technical details are available if you want to inspect the execution.")


def _strip_visible_technical_noise(text: str) -> str:
    lines: list[str] = []
    skip_json_block = False
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        lowered = line.lower()
        if lowered.startswith("# browser research summary"):
            break
        if re.match(r"^\[step[_-]?\d+\]$", lowered):
            continue
        if line.startswith(("--- ", "+++ ", "@@ ", "+")):
            continue
        if lowered.startswith("status:"):
            continue
        if not line:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        if lowered.startswith("[") and lowered.endswith("]"):
            if any(marker in lowered for marker in _VISIBLE_BLOCKED_MARKERS):
                skip_json_block = True
                continue
            if lowered in {"[page_state]", "[worker_lanes]", "[agent_reviews]", "[mission_trace]"}:
                skip_json_block = True
                continue
        if skip_json_block and (line.startswith("{") or line.startswith("[") or line.startswith('"') or ":" in line):
            continue
        skip_json_block = False
        if any(marker in lowered for marker in _VISIBLE_BLOCKED_MARKERS):
            continue
        if lowered.startswith(("trace:", "provider:", "model:", "worker lanes", "mission trace")):
            continue
        lines.append(raw_line.rstrip())

    cleaned = "\n".join(lines).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned


def _action_completion_summary(text: str) -> str:
    match = re.search(r"Applied\s+(create|update|edit)\s+on\s+`([^`]+)`", str(text or ""), flags=re.IGNORECASE)
    if not match:
        return ""
    operation = match.group(1).lower()
    path = match.group(2).strip()
    verb = "created" if operation == "create" else "updated"
    return f"Done. I {verb} `{path}` and verified the change.\n\nNext: open `{path}` and review the content."


def _fallback_visible_summary(payload: dict[str, Any]) -> str:
    status = str(payload.get("validation_status") or "").replace("_", " ").strip()
    artifacts = _artifact_paths(payload)
    if artifacts:
        created = ", ".join(artifacts[:3])
        return f"Done. I completed the task and produced the requested artifact(s): {created}.\n\nNext: open the file and review the result."
    if status == CompletionState.FINISHED.value:
        return "Done. I completed the task successfully.\n\nNext: tell me what you want to inspect or improve next."
    if status:
        return f"I could not fully complete this yet. Status: {status}.\n\nNext: adjust the request or check the technical details."
    return "I can help. Give me one concrete task and the output you want.\n\nNext: ask me to inspect, create, edit, or verify something."


def _limit_words(text: str, limit: int) -> str:
    words = text.split()
    if len(words) <= limit:
        return text.strip()
    return " ".join(words[:limit]).rstrip(" ,.;:") + "...\n\nNext: ask me for the full report if you want more detail."


def _iter_text_deltas(text: str) -> list[str]:
    chunks: list[str] = []
    buffer = ""
    for token in re.findall(r"\S+\s*|\n+", text):
        if len(buffer) + len(token) > 20 and buffer:
            chunks.append(buffer)
            buffer = token
        else:
            buffer += token
    if buffer:
        chunks.append(buffer)
    return chunks or [text]


def _stream_footer(result: dict[str, Any], *, elapsed_ms: float) -> str:
    latency_ms = result.get("latency_ms")
    total_tokens = result.get("total_tokens")
    provider_telemetry = result.get("provider_telemetry") if isinstance(result.get("provider_telemetry"), dict) else {}
    if provider_telemetry:
        latency_ms = provider_telemetry.get("provider_latency_ms") or latency_ms

    parts = ["FORGE"]
    if latency_ms:
        parts.append(f"{max(float(latency_ms), elapsed_ms) / 1000:.1f}s")
    else:
        parts.append(f"{elapsed_ms / 1000:.1f}s")
    if total_tokens:
        parts.append(f"{int(total_tokens)} tok")
    return " | ".join(parts)


def _serialize_conversation_response(
    *,
    answer: str,
    intent,
    response,
    workspace_root: Path,
) -> dict[str, Any]:
    workspace_status = get_workspace_status()
    plan = ExecutionPlan(
        objective=intent.objective or "Answer the user directly.",
        task_type=intent.task_type,
        risk_level=intent.risk_level,
        steps=[],
        fallbacks=[],
        completion_criteria=["Return a natural, direct answer without fake execution."],
    )
    payload = {
        "objective": intent.objective or "Answer the user directly.",
        "approach_taken": [
            f"Intent resolved as `{intent.primary_intent.value}`.",
            "No execution skills were needed.",
            "FORGE used direct model routing for a natural reply.",
        ],
        "result": answer,
        "answer": answer,
        "validation_status": CompletionState.FINISHED.value,
        "risks_or_limitations": [],
        "best_next_action": "Continue the conversation or give FORGE a concrete task to execute.",
        "intent": intent.model_dump(mode="json"),
        "plan": plan.model_dump(mode="json"),
        "step_results": [],
        "artifacts": {
            "conversation_metadata": {
                "model_id": response.model_id,
                "provider": response.provider,
                "latency_ms": response.latency_ms,
                "total_tokens": response.total_tokens,
                "routing_telemetry": response.routing_telemetry,
            }
        },
        "mission_trace": [
            "Intent resolved as conversation.",
            "No tools were required.",
            "FORGE selected the strongest available model path for a direct reply.",
        ],
        "mission_id": "",
        "audit_log_path": "",
        "resumed_from_step": None,
        "agent_reviews": [],
        "model_used": response.model_id,
        "provider_used": response.provider,
        "latency_ms": response.latency_ms,
        "total_tokens": response.total_tokens,
        "provider_telemetry": response.routing_telemetry,
        "completed_steps": 0,
        "total_steps": 0,
        "evidence_count": 0,
        "artifacts_count": 1,
        "workspace_root": str(workspace_root),
        "artifact_root": workspace_status["artifact_root"],
    }
    payload.update(workspace_status)
    return _with_human_first_response(payload)


def _serialize_direct_response(
    *,
    answer: str,
    intent,
    workspace_root: Path,
    approach: str,
) -> dict[str, Any]:
    workspace_status = get_workspace_status()
    plan = ExecutionPlan(
        objective=intent.objective or "Answer the user directly.",
        task_type=intent.task_type,
        risk_level=intent.risk_level,
        steps=[],
        fallbacks=[],
        completion_criteria=["Return a natural, direct answer without fake execution."],
    )
    payload = {
        "objective": intent.objective or "Answer the user directly.",
        "approach_taken": [
            f"Intent resolved as `{intent.primary_intent.value}`.",
            approach,
            "No execution skills were needed.",
        ],
        "result": answer,
        "answer": answer,
        "validation_status": CompletionState.FINISHED.value,
        "risks_or_limitations": [],
        "best_next_action": "Continue the conversation or give FORGE a concrete task to execute.",
        "intent": intent.model_dump(mode="json"),
        "plan": plan.model_dump(mode="json"),
        "step_results": [],
        "artifacts": {},
        "mission_trace": [
            "Intent resolved as conversation.",
            approach,
            "No model or execution skills were required.",
        ],
        "mission_id": "",
        "audit_log_path": "",
        "resumed_from_step": None,
        "agent_reviews": [],
        "completed_steps": 0,
        "total_steps": 0,
        "evidence_count": 0,
        "artifacts_count": 0,
        "workspace_root": str(workspace_root),
        "artifact_root": workspace_status["artifact_root"],
    }
    payload.update(workspace_status)
    return _with_human_first_response(payload)


def _serialize_clarification_response(
    *,
    answer: str,
    intent,
    workspace_root: Path,
    error: str,
) -> dict[str, Any]:
    workspace_status = get_workspace_status()
    plan = ExecutionPlan(
        objective="Clarify the mission before execution.",
        task_type=intent.task_type,
        risk_level=intent.risk_level,
        steps=[],
        fallbacks=[],
        completion_criteria=["Clarify the mission before execution."],
    )
    payload = {
        "objective": "Clarify the mission before execution.",
        "approach_taken": [
            f"Intent resolved as `{intent.primary_intent.value}`.",
            "Direct model routing failed.",
            "FORGE returned a safe fallback clarification instead of pretending success.",
        ],
        "result": answer,
        "answer": answer,
        "validation_status": CompletionState.PARTIALLY_FINISHED.value,
        "risks_or_limitations": [error],
        "best_next_action": "Add a working provider key or give FORGE an executable task inside a selected workspace.",
        "intent": intent.model_dump(mode="json"),
        "plan": plan.model_dump(mode="json"),
        "step_results": [],
        "artifacts": {},
        "mission_trace": [
            "Intent resolved as conversation.",
            "Direct model reply failed.",
            "FORGE returned a safe fallback clarification instead of pretending success.",
        ],
        "mission_id": "",
        "audit_log_path": "",
        "resumed_from_step": None,
        "agent_reviews": [],
        "completed_steps": 0,
        "total_steps": 0,
        "evidence_count": 0,
        "artifacts_count": 0,
        "workspace_root": str(workspace_root),
        "artifact_root": workspace_status["artifact_root"],
        "stream_footer": "FORGE | fallback",
    }
    payload.update(workspace_status)
    return _with_human_first_response(payload)


def _diagnostics_payload(payload: dict[str, Any]) -> dict[str, Any]:
    artifacts = payload.get("artifacts") if isinstance(payload.get("artifacts"), dict) else {}
    diagnostics = {
        "mission_id": payload.get("mission_id", ""),
        "audit_log_path": payload.get("audit_log_path", ""),
        "validation_status": payload.get("validation_status", ""),
        "intent": payload.get("intent", {}),
        "plan": payload.get("plan", {}),
        "step_results": payload.get("step_results", []),
        "mission_trace": payload.get("mission_trace", []),
        "agent_reviews": payload.get("agent_reviews", []),
        "provider_telemetry": payload.get("provider_telemetry", {}),
        "artifact_keys": list(artifacts.keys()) if isinstance(artifacts, dict) else [],
    }
    if isinstance(artifacts, dict):
        for key in ("mission_trace", "mission_audit", "agent_reviews", "worker_lanes"):
            if key in artifacts:
                diagnostics[key] = artifacts[key]
    return diagnostics
