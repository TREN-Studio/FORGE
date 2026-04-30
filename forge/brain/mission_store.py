from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from forge.brain.contracts import StepExecutionResult
from forge.config.settings import OperatorSettings
from forge.runtime.state_store import PersistentStateStore


@dataclass(slots=True)
class MissionResumeState:
    mission_id: str
    audit_log_path: str
    completed_steps: list[StepExecutionResult]
    artifacts: dict
    mission_trace: list[str]
    resumed_from_step: str | None
    saved_plan: dict


class MissionAuditStore:
    """Persist mission execution state for auditability and resume support."""

    def __init__(self, settings: OperatorSettings) -> None:
        self._settings = settings
        self._state_store = PersistentStateStore(settings.state_db_path, encryption_key_path=settings.approval_key_path)

    @property
    def root(self) -> Path:
        path = self._settings.artifact_root / "missions"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def begin(
        self,
        request: str,
        plan,
        *,
        resume_mission_id: str | None = None,
    ) -> tuple[str, str, MissionResumeState | None]:
        if resume_mission_id:
            resume_state = self.load_resume_state(resume_mission_id)
            if resume_state is not None:
                existing_payload = self._state_store.get_mission(resume_state.mission_id)
                if existing_payload is None:
                    existing_payload = self._read_json(Path(resume_state.audit_log_path))
                resumed_payload = {
                    **existing_payload,
                    "request": request,
                    "plan": plan.model_dump(mode="json"),
                    "status": "resuming",
                    "updated_at": self._timestamp(),
                }
                self._write_json(
                    Path(resume_state.audit_log_path),
                    resumed_payload,
                )
                self._state_store.upsert_mission(
                    resume_state.mission_id,
                    audit_log_path=resume_state.audit_log_path,
                    request=request,
                    plan=resumed_payload["plan"],
                    status="resuming",
                    step_results=resumed_payload.get("step_results", []),
                    artifacts=resumed_payload.get("artifacts", {}),
                    mission_trace=resumed_payload.get("mission_trace", []),
                    resumed_from_step=resumed_payload.get("resumed_from_step"),
                )
                return resume_state.mission_id, resume_state.audit_log_path, resume_state

        mission_id = f"mission-{uuid4().hex[:12]}"
        audit_path = self.root / f"{mission_id}.json"
        payload = {
            "mission_id": mission_id,
            "request": request,
            "plan": plan.model_dump(mode="json"),
            "status": "running",
            "created_at": self._timestamp(),
            "updated_at": self._timestamp(),
            "step_results": [],
            "artifacts": {},
            "mission_trace": [],
            "resumed_from_step": None,
        }
        self._write_json(audit_path, payload)
        self._state_store.upsert_mission(
            mission_id,
            audit_log_path=str(audit_path),
            request=request,
            plan=payload["plan"],
            status=payload["status"],
            step_results=payload["step_results"],
            artifacts=payload["artifacts"],
            mission_trace=payload["mission_trace"],
            resumed_from_step=payload["resumed_from_step"],
        )
        return mission_id, str(audit_path), None

    def save_progress(
        self,
        mission_id: str,
        audit_log_path: str,
        *,
        request: str,
        plan,
        status: str,
        step_results: list[StepExecutionResult],
        artifacts: dict,
        mission_trace: list[str],
        resumed_from_step: str | None,
    ) -> None:
        payload = {
            "mission_id": mission_id,
            "request": request,
            "plan": plan.model_dump(mode="json"),
            "status": status,
            "updated_at": self._timestamp(),
            "step_results": [step.model_dump(mode="json") for step in step_results],
            "artifacts": artifacts,
            "mission_trace": mission_trace,
            "resumed_from_step": resumed_from_step,
        }
        file_path = Path(audit_log_path)
        if file_path.exists():
            existing = self._read_json(file_path)
            payload["created_at"] = existing.get("created_at", self._timestamp())
        else:
            payload["created_at"] = self._timestamp()
        self._write_json(file_path, payload)
        self._state_store.upsert_mission(
            mission_id,
            audit_log_path=str(file_path),
            request=request,
            plan=payload["plan"],
            status=status,
            step_results=payload["step_results"],
            artifacts=artifacts,
            mission_trace=mission_trace,
            resumed_from_step=resumed_from_step,
        )

    def load_resume_state(self, mission_id: str) -> MissionResumeState | None:
        payload = self._state_store.get_mission(mission_id)
        if payload is None:
            file_path = self.root / f"{mission_id}.json"
            if not file_path.exists():
                return None
            payload = self._read_json(file_path)
        file_path = Path(payload["audit_log_path"])
        raw_results = payload.get("step_results", [])
        completed_steps: list[StepExecutionResult] = []
        for raw in raw_results:
            step = StepExecutionResult.model_validate(raw)
            if step.status.value == "finished" and not step.rolled_back:
                completed_steps.append(step)
        resumed_from_step = completed_steps[-1].step_id if completed_steps else None
        return MissionResumeState(
            mission_id=mission_id,
            audit_log_path=str(file_path),
            completed_steps=completed_steps,
            artifacts=payload.get("artifacts", {}),
            mission_trace=list(payload.get("mission_trace", [])),
            resumed_from_step=resumed_from_step,
            saved_plan=payload.get("plan", {}),
        )

    @property
    def state_store(self) -> PersistentStateStore:
        return self._state_store

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _read_json(path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _write_json(path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
