from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class WorkerTask(BaseModel):
    task_id: str = Field(default_factory=lambda: f"task-{uuid4().hex[:12]}")
    service_name: str
    operation: str
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str = Field(default_factory=lambda: f"idem-{uuid4().hex}")
    mission_id: str = ""
    step_id: str = ""
    remote_allowed: bool = True
    lease_ttl_seconds: int = 30
    timeout_seconds: int = 60
    created_at: str = Field(default_factory=_utcnow)


class WorkerTaskResult(BaseModel):
    task_id: str
    service_name: str
    operation: str
    status: str
    output: Any = None
    error: str = ""
    worker_id: str = ""
    ticket_id: str = ""
    cached: bool = False
    metrics: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_utcnow)


class WorkerRegistration(BaseModel):
    worker_id: str
    endpoint_url: str
    services: list[str]
    capabilities: dict[str, Any] = Field(default_factory=dict)
    process_mode: str = "process"
    lease_ttl_seconds: int = 30
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkerHeartbeat(BaseModel):
    worker_id: str
    status: str
    metrics: dict[str, Any] = Field(default_factory=dict)
    lease_ttl_seconds: int | None = None
