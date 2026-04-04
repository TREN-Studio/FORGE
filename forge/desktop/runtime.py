from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import threading
from typing import Any

from forge import __version__
from forge.brain.contracts import OperatorResult
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
        )
    )
    result = operator.handle(prompt, confirmed=confirmed, dry_run=dry_run)
    return _serialize_operator_result(result, operator, normalized_workspace_root)


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
    payload.update(workspace_status)
    payload["workspace_root"] = str(workspace_root)
    payload["artifact_root"] = str(operator.settings.artifact_root)
    return payload
