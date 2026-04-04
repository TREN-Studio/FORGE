from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class GatewayEnvelope(BaseModel):
    type: Literal["message", "ping"] = "message"
    request_id: str = Field(default_factory=lambda: uuid4().hex)
    session_id: str = Field(default_factory=lambda: uuid4().hex)
    user_id: str = "local-user"
    channel: str = "webchat"
    lane: str | None = None
    content: str = ""
    confirmed: bool = False
    dry_run: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_utcnow_iso)

    def normalized_lane(self) -> str:
        if self.lane:
            return self.lane.strip() or self.session_id
        return self.session_id or self.user_id or "default"


class AgentReply(BaseModel):
    request_id: str
    session_id: str
    lane: str
    channel: str
    status: Literal["completed", "failed", "blocked"] = "completed"
    objective: str
    answer: str
    validation_status: str
    best_next_action: str
    approach_taken: list[str] = Field(default_factory=list)
    plan: dict[str, Any] = Field(default_factory=dict)
    step_results: list[dict[str, Any]] = Field(default_factory=list)
    artifacts: dict[str, Any] = Field(default_factory=dict)
    memory_sources: list[str] = Field(default_factory=list)
    memory_excerpt: str = ""
    evidence_count: int = 0
    processing_ms: float = 0.0
    created_at: str = Field(default_factory=_utcnow_iso)


class HeartbeatReport(BaseModel):
    task_name: str
    status: Literal["completed", "failed", "skipped"] = "completed"
    duration_ms: float = 0.0
    details: dict[str, Any] = Field(default_factory=dict)
    started_at: str = Field(default_factory=_utcnow_iso)
