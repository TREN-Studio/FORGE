from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from queue import Empty, Queue
import re
import threading
import time
from typing import Any

from forge import __version__
from forge.brain.contracts import CompletionState, ExecutionPlan, IntentKind, OperatorResult
from forge.brain.operator import ForgeOperator
from forge.config.settings import OperatorSettings
from forge.core.session import ForgeSession
from forge.tools.workspace import WorkspaceTools


@dataclass(slots=True)
class DesktopBootStatus:
    providers: int
    models_online: int
    summary: str
    workspace_root: str
    artifact_root: str


@dataclass(slots=True)
class DesktopWorkspaceState:
    workspace_root: Path
    state_file: Path


_STATE_LOCK = threading.Lock()


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


def boot_status() -> DesktopBootStatus:
    session = ForgeSession(memory=False)
    status = session._router.status()
    providers = status.get("providers", 0)
    models_online = status.get("models_online", 0)
    workspace = get_workspace_status()
    summary = (
        f"FORGE v{__version__} booted with {providers} provider(s) "
        f"and {models_online} live model(s). Active workspace: {workspace['workspace_root']}."
    )
    return DesktopBootStatus(
        providers=providers,
        models_online=models_online,
        summary=summary,
        workspace_root=workspace["workspace_root"],
        artifact_root=workspace["artifact_root"],
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

    normalized_workspace_root = _normalize_workspace_root(workspace_root)
    if workspace_root is not None:
        set_workspace_root(normalized_workspace_root)

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

    normalized_workspace_root = _normalize_workspace_root(workspace_root)
    if workspace_root is not None:
        set_workspace_root(normalized_workspace_root)

    operator = ForgeOperator(
        settings=OperatorSettings(
            enable_memory=False,
            workspace_root=normalized_workspace_root,
        ),
        provider_secrets=provider_secrets,
    )
    intent = operator.intent_resolver.resolve(prompt)
    routing = operator.skill_router.route(intent, operator.registry.list())

    if intent.primary_intent == IntentKind.CONVERSATION and not routing.selected_skills:
        started = time.monotonic()
        yield {
            "type": "status",
            "stage": "routing",
            "message": "Selecting the strongest available provider path...",
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
                        "type": "status",
                        "stage": "routing",
                        "message": f"Using {display_name} on {provider}.",
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
                "type": "done",
                "done": True,
                "payload": payload,
                "footer": footer,
            }
            return
        except Exception as exc:
            fallback = operator._clarification_text(prompt)
            payload = _serialize_clarification_response(
                answer=fallback,
                intent=intent,
                workspace_root=normalized_workspace_root,
                error=str(exc),
            )
            for delta in _iter_text_deltas(fallback):
                yield {"type": "delta", "delta": delta}
            yield {
                "type": "done",
                "done": True,
                "payload": payload,
                "footer": payload.get("stream_footer", ""),
            }
            return

    events: Queue[tuple[str, Any]] = Queue()
    started = time.monotonic()
    spinner_messages = [
        "Selecting the strongest available model path...",
        "Planning the response inside your workspace...",
        "Running the best path FORGE found for this request...",
    ]

    def worker() -> None:
        try:
            result = operate_prompt(
                prompt,
                confirmed=confirmed,
                dry_run=dry_run,
                workspace_root=normalized_workspace_root,
                provider_secrets=provider_secrets,
            )
            events.put(("result", result))
        except Exception as exc:  # pragma: no cover - surfaced to SSE client
            events.put(("error", str(exc)))
        finally:
            events.put(("done", None))

    yield {
        "type": "status",
        "stage": "routing",
        "message": "Selecting the strongest available provider path...",
    }
    yield {
        "type": "status",
        "stage": "workspace",
        "message": f"Using workspace: {normalized_workspace_root}",
    }

    threading.Thread(target=worker, daemon=True).start()
    heartbeat_index = 0
    result_emitted = False

    while True:
        try:
            kind, payload = events.get(timeout=0.45)
        except Empty:
            if not result_emitted:
                yield {
                    "type": "status",
                    "stage": "running",
                    "message": spinner_messages[heartbeat_index % len(spinner_messages)],
                }
                heartbeat_index += 1
            continue

        if kind == "error":
            yield {"type": "error", "error": str(payload)}
            result_emitted = True
            continue

        if kind == "result":
            result = payload
            yield {
                "type": "status",
                "stage": "streaming",
                "message": "Response ready. Streaming output...",
            }
            answer = str(result.get("answer") or result.get("result") or "No result produced.")
            footer = _stream_footer(result, elapsed_ms=(time.monotonic() - started) * 1000)
            result["stream_footer"] = footer
            for delta in _iter_text_deltas(answer):
                yield {"type": "delta", "delta": delta}
            yield {
                "type": "done",
                "done": True,
                "payload": result,
                "footer": footer,
            }
            result_emitted = True
            continue

        if kind == "done":
            break


def boot_status_for_user(provider_secrets: dict[str, dict[str, str]] | None = None) -> DesktopBootStatus:
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
    return DesktopBootStatus(
        providers=providers,
        models_online=models_online,
        summary=summary,
        workspace_root=workspace["workspace_root"],
        artifact_root=workspace["artifact_root"],
    )


def _serialize_operator_result(
    result: OperatorResult,
    operator: ForgeOperator,
    workspace_root: Path,
) -> dict[str, Any]:
    workspace_status = get_workspace_status()
    payload = result.model_dump(mode="json")
    payload["answer"] = operator.composer.compose(result)
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
    return payload


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
    model = str(result.get("model_used") or "").strip()
    provider = str(result.get("provider_used") or "").strip()
    latency_ms = result.get("latency_ms")
    total_tokens = result.get("total_tokens")
    provider_telemetry = result.get("provider_telemetry") if isinstance(result.get("provider_telemetry"), dict) else {}
    if provider_telemetry:
        provider = str(provider_telemetry.get("final_provider_used") or provider).strip()
        latency_ms = provider_telemetry.get("provider_latency_ms") or latency_ms

    if not model and isinstance(result.get("step_results"), list) and result["step_results"]:
        final_skill = result["step_results"][-1].get("skill") or result["step_results"][-1].get("tool")
        model = f"{final_skill or 'mission'}"

    label = provider or model or "FORGE"
    parts = [label]
    if latency_ms:
        parts.append(f"{max(float(latency_ms), elapsed_ms) / 1000:.1f}s")
    else:
        parts.append(f"{elapsed_ms / 1000:.1f}s")
    if total_tokens:
        parts.append(f"{int(total_tokens)} tok")
    if provider_telemetry and int(provider_telemetry.get("fallback_count") or 0):
        parts.append(f"fallbacks={int(provider_telemetry.get('fallback_count') or 0)}")
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
    return payload


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
    return payload
