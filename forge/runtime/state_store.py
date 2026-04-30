from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from cryptography.fernet import Fernet


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _hash_password(password: str, salt: bytes) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 310_000)
    return digest.hex()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _mask_secret(value: str) -> str:
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}{'*' * max(0, len(value) - 8)}{value[-4:]}"


@dataclass(slots=True)
class TaskClaim:
    status: str
    ticket_id: str
    cached_result: Any = None
    worker_id: str = ""
    lease_expires_at: float = 0.0


class PersistentStateStore:
    """SQLite-backed platform state store for missions, approvals, workers, and task leases."""

    def __init__(
        self,
        database_path: Path,
        *,
        encryption_key_path: Path | None = None,
        backend: str = "sqlite",
    ) -> None:
        if backend != "sqlite":
            raise ValueError(f"Unsupported state backend: {backend}")
        self._path = database_path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._key_path = encryption_key_path or database_path.with_suffix(".key")
        self._key_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._fernet = Fernet(self._load_or_create_key())
        self._init_schema()

    def upsert_mission(
        self,
        mission_id: str,
        *,
        audit_log_path: str,
        request: str,
        plan: dict[str, Any],
        status: str,
        step_results: list[dict[str, Any]],
        artifacts: dict[str, Any],
        mission_trace: list[str],
        resumed_from_step: str | None,
    ) -> None:
        now = _utcnow()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO mission_audits (
                    mission_id, audit_log_path, request, plan_json, status,
                    step_results_json, artifacts_json, mission_trace_json,
                    resumed_from_step, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(mission_id) DO UPDATE SET
                    audit_log_path = excluded.audit_log_path,
                    request = excluded.request,
                    plan_json = excluded.plan_json,
                    status = excluded.status,
                    step_results_json = excluded.step_results_json,
                    artifacts_json = excluded.artifacts_json,
                    mission_trace_json = excluded.mission_trace_json,
                    resumed_from_step = excluded.resumed_from_step,
                    updated_at = excluded.updated_at
                """,
                (
                    mission_id,
                    audit_log_path,
                    request,
                    _json_dumps(plan),
                    status,
                    _json_dumps(step_results),
                    _json_dumps(artifacts),
                    _json_dumps(mission_trace),
                    resumed_from_step,
                    now,
                    now,
                ),
            )

    def get_mission(self, mission_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM mission_audits WHERE mission_id = ?",
                (mission_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "mission_id": row["mission_id"],
            "audit_log_path": row["audit_log_path"],
            "request": row["request"],
            "plan": json.loads(row["plan_json"]),
            "status": row["status"],
            "step_results": json.loads(row["step_results_json"]),
            "artifacts": json.loads(row["artifacts_json"]),
            "mission_trace": json.loads(row["mission_trace_json"]),
            "resumed_from_step": row["resumed_from_step"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def save_critique_notes(self, mission_id: str, step_id: str, skill_name: str, notes: list[str]) -> None:
        now = _utcnow()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO critique_memory (
                    mission_id, step_id, skill_name, notes_json, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(mission_id, step_id, skill_name) DO UPDATE SET
                    notes_json = excluded.notes_json,
                    updated_at = excluded.updated_at
                """,
                (mission_id, step_id, skill_name, _json_dumps(notes), now),
            )

    def load_critique_memory(self, mission_id: str) -> dict[str, list[str]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT step_id, skill_name, notes_json FROM critique_memory WHERE mission_id = ?",
                (mission_id,),
            ).fetchall()
        critique: dict[str, list[str]] = {}
        for row in rows:
            key = f"{row['step_id']}::{row['skill_name']}"
            critique[key] = list(dict.fromkeys(json.loads(row["notes_json"])))
        return critique

    def create_pending_approval(
        self,
        *,
        mission_id: str,
        step_id: str,
        approval_class: str,
        request_excerpt: str,
        payload: dict[str, Any],
        summary: str,
        policy_mode: str,
        expires_at: str | None = None,
    ) -> str:
        approval_id = f"approval-{uuid4().hex[:12]}"
        now = _utcnow()
        encrypted = self._fernet.encrypt(_json_dumps(payload).encode("utf-8"))
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO approvals (
                    approval_id, mission_id, step_id, approval_class, status,
                    request_excerpt, summary, encrypted_payload, policy_mode,
                    created_at, updated_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval_id,
                    mission_id,
                    step_id,
                    approval_class,
                    "pending",
                    request_excerpt,
                    summary,
                    encrypted,
                    policy_mode,
                    now,
                    now,
                    expires_at,
                ),
            )
        return approval_id

    def get_approval(self, approval_id: str, *, include_payload: bool = False) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM approvals WHERE approval_id = ?",
                (approval_id,),
            ).fetchone()
        if row is None:
            return None
        payload = {
            "approval_id": row["approval_id"],
            "mission_id": row["mission_id"],
            "step_id": row["step_id"],
            "approval_class": row["approval_class"],
            "status": row["status"],
            "request_excerpt": row["request_excerpt"],
            "summary": row["summary"],
            "policy_mode": row["policy_mode"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "expires_at": row["expires_at"],
            "approved_at": row["approved_at"],
            "rejected_at": row["rejected_at"],
            "decision_notes": row["decision_notes"] or "",
        }
        if include_payload:
            payload["payload"] = json.loads(self._fernet.decrypt(row["encrypted_payload"]).decode("utf-8"))
        return payload

    def list_approvals(self, *, status: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM approvals"
        params: tuple[Any, ...] = ()
        if status:
            query += " WHERE status = ?"
            params = (status,)
        query += " ORDER BY created_at DESC"
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [
            {
                "approval_id": row["approval_id"],
                "mission_id": row["mission_id"],
                "step_id": row["step_id"],
                "approval_class": row["approval_class"],
                "status": row["status"],
                "summary": row["summary"],
                "request_excerpt": row["request_excerpt"],
                "policy_mode": row["policy_mode"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "expires_at": row["expires_at"],
            }
            for row in rows
        ]

    def decide_approval(self, approval_id: str, *, approved: bool, notes: str = "") -> dict[str, Any] | None:
        now = _utcnow()
        status = "approved" if approved else "rejected"
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                UPDATE approvals
                SET status = ?, updated_at = ?, approved_at = ?, rejected_at = ?, decision_notes = ?
                WHERE approval_id = ?
                """,
                (
                    status,
                    now,
                    now if approved else None,
                    None if approved else now,
                    notes,
                    approval_id,
                ),
            )
            if cursor.rowcount == 0:
                return None
        return self.get_approval(approval_id, include_payload=False)

    def claim_task(
        self,
        *,
        idempotency_key: str,
        worker_id: str,
        service_name: str,
        operation: str,
        mission_id: str,
        step_id: str,
        lease_ttl_seconds: int,
    ) -> TaskClaim:
        now = time.time()
        expires_at = now + max(1, lease_ttl_seconds)
        ticket_id = f"ticket-{uuid4().hex[:12]}"
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT * FROM task_leases WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if row is None:
                self._conn.execute(
                    """
                    INSERT INTO task_leases (
                        idempotency_key, service_name, operation, mission_id, step_id,
                        worker_id, ticket_id, status, lease_expires_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        idempotency_key,
                        service_name,
                        operation,
                        mission_id,
                        step_id,
                        worker_id,
                        ticket_id,
                        "leased",
                        expires_at,
                        _utcnow(),
                        _utcnow(),
                    ),
                )
                return TaskClaim(status="claimed", ticket_id=ticket_id, worker_id=worker_id, lease_expires_at=expires_at)

            if row["status"] == "completed" and row["result_json"]:
                return TaskClaim(
                    status="cached",
                    ticket_id=row["ticket_id"],
                    cached_result=json.loads(row["result_json"]),
                    worker_id=row["worker_id"],
                    lease_expires_at=float(row["lease_expires_at"] or 0.0),
                )

            lease_expires_at = float(row["lease_expires_at"] or 0.0)
            if row["status"] in {"leased", "running"} and lease_expires_at > now:
                return TaskClaim(
                    status="busy",
                    ticket_id=row["ticket_id"],
                    worker_id=row["worker_id"],
                    lease_expires_at=lease_expires_at,
                )

            self._conn.execute(
                """
                UPDATE task_leases
                SET worker_id = ?, ticket_id = ?, status = ?, lease_expires_at = ?, error = '', updated_at = ?
                WHERE idempotency_key = ?
                """,
                (
                    worker_id,
                    ticket_id,
                    "leased",
                    expires_at,
                    _utcnow(),
                    idempotency_key,
                ),
            )
        return TaskClaim(status="claimed", ticket_id=ticket_id, worker_id=worker_id, lease_expires_at=expires_at)

    def mark_task_running(self, *, idempotency_key: str, ticket_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE task_leases SET status = 'running', updated_at = ? WHERE idempotency_key = ? AND ticket_id = ?",
                (_utcnow(), idempotency_key, ticket_id),
            )

    def complete_task(self, *, idempotency_key: str, ticket_id: str, result: Any) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE task_leases
                SET status = 'completed', result_json = ?, error = '', updated_at = ?, lease_expires_at = ?
                WHERE idempotency_key = ? AND ticket_id = ?
                """,
                (_json_dumps(result), _utcnow(), time.time(), idempotency_key, ticket_id),
            )

    def fail_task(self, *, idempotency_key: str, ticket_id: str, error: str, release: bool) -> None:
        status = "failed" if release else "running"
        lease_expires_at = time.time() if release else None
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE task_leases
                SET status = ?, error = ?, updated_at = ?, lease_expires_at = COALESCE(?, lease_expires_at)
                WHERE idempotency_key = ? AND ticket_id = ?
                """,
                (status, error[:4000], _utcnow(), lease_expires_at, idempotency_key, ticket_id),
            )

    def register_worker(
        self,
        *,
        worker_id: str,
        endpoint_url: str,
        services: list[str],
        capabilities: dict[str, Any],
        process_mode: str,
        lease_ttl_seconds: int,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        now = time.time()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO workers (
                    worker_id, endpoint_url, services_json, capabilities_json,
                    process_mode, lease_ttl_seconds, last_seen_at, metrics_json,
                    status, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(worker_id) DO UPDATE SET
                    endpoint_url = excluded.endpoint_url,
                    services_json = excluded.services_json,
                    capabilities_json = excluded.capabilities_json,
                    process_mode = excluded.process_mode,
                    lease_ttl_seconds = excluded.lease_ttl_seconds,
                    last_seen_at = excluded.last_seen_at,
                    status = excluded.status,
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at
                """,
                (
                    worker_id,
                    endpoint_url,
                    _json_dumps(services),
                    _json_dumps(capabilities),
                    process_mode,
                    lease_ttl_seconds,
                    now,
                    _json_dumps({}),
                    "idle",
                    _json_dumps(metadata or {}),
                    _utcnow(),
                    _utcnow(),
                ),
            )

    def heartbeat_worker(
        self,
        *,
        worker_id: str,
        status: str,
        metrics: dict[str, Any],
        lease_ttl_seconds: int | None = None,
    ) -> None:
        now = time.time()
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE workers
                SET status = ?, metrics_json = ?, last_seen_at = ?,
                    lease_ttl_seconds = COALESCE(?, lease_ttl_seconds),
                    updated_at = ?
                WHERE worker_id = ?
                """,
                (status, _json_dumps(metrics), now, lease_ttl_seconds, _utcnow(), worker_id),
            )

    def list_workers(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM workers ORDER BY created_at ASC").fetchall()
            lease_rows = self._conn.execute(
                """
                SELECT worker_id, COUNT(*) AS active_leases
                FROM task_leases
                WHERE status IN ('leased', 'running') AND lease_expires_at > ?
                GROUP BY worker_id
                """,
                (time.time(),),
            ).fetchall()
        active_lease_counts = {row["worker_id"]: int(row["active_leases"]) for row in lease_rows}
        now = time.time()
        workers: list[dict[str, Any]] = []
        for row in rows:
            ttl = int(row["lease_ttl_seconds"] or 0)
            last_seen = float(row["last_seen_at"] or 0.0)
            stale = ttl > 0 and (now - last_seen) > ttl
            metrics = json.loads(row["metrics_json"] or "{}")
            inflight = active_lease_counts.get(row["worker_id"], 0)
            metrics["active_jobs"] = max(int(metrics.get("active_jobs", 0)), inflight)
            metrics["queue_length"] = max(
                int(metrics.get("queue_length", int(metrics.get("queued_jobs", 0)) + int(metrics.get("active_jobs", 0)))),
                inflight + int(metrics.get("queued_jobs", 0)),
            )
            workers.append(
                {
                    "worker_id": row["worker_id"],
                    "endpoint_url": row["endpoint_url"],
                    "services": json.loads(row["services_json"]),
                    "capabilities": json.loads(row["capabilities_json"]),
                    "process_mode": row["process_mode"],
                    "lease_ttl_seconds": ttl,
                    "last_seen_at": last_seen,
                    "status": "failed" if stale else (row["status"] or "idle"),
                    "metrics": metrics,
                    "metadata": json.loads(row["metadata_json"] or "{}"),
                    "is_stale": stale,
                    "updated_at": row["updated_at"],
                }
            )
        return workers

    def create_user(
        self,
        *,
        email: str,
        password: str,
        display_name: str = "",
        admin_email: str = "",
    ) -> dict[str, Any]:
        normalized_email = email.strip().lower()
        if not normalized_email or "@" not in normalized_email:
            raise ValueError("Valid email is required.")
        if len(password) < 8:
            raise ValueError("Password must be at least 8 characters.")
        user_id = f"user-{uuid4().hex[:12]}"
        salt = secrets.token_bytes(16)
        password_hash = _hash_password(password, salt)
        is_admin = normalized_email == admin_email.strip().lower()
        now = _utcnow()
        with self._lock, self._conn:
            existing = self._conn.execute(
                "SELECT user_id FROM users WHERE email = ?",
                (normalized_email,),
            ).fetchone()
            if existing is not None:
                raise ValueError("An account with this email already exists.")
            self._conn.execute(
                """
                INSERT INTO users (
                    user_id, email, display_name, password_hash, password_salt,
                    is_admin, created_at, updated_at, last_login_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    normalized_email,
                    display_name.strip(),
                    password_hash,
                    salt.hex(),
                    1 if is_admin else 0,
                    now,
                    now,
                    None,
                ),
            )
        return self.get_user(user_id) or {}

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "user_id": row["user_id"],
            "email": row["email"],
            "display_name": row["display_name"] or "",
            "is_admin": bool(row["is_admin"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "last_login_at": row["last_login_at"],
        }

    def authenticate_user(self, *, email: str, password: str) -> dict[str, Any] | None:
        normalized_email = email.strip().lower()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM users WHERE email = ?",
                (normalized_email,),
            ).fetchone()
        if row is None:
            return None
        salt = bytes.fromhex(row["password_salt"])
        expected = row["password_hash"]
        candidate = _hash_password(password, salt)
        if not hmac.compare_digest(candidate, expected):
            return None
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE users SET last_login_at = ?, updated_at = ? WHERE user_id = ?",
                (_utcnow(), _utcnow(), row["user_id"]),
            )
        return self.get_user(row["user_id"])

    def create_session(self, *, user_id: str, ttl_days: int = 30) -> dict[str, Any]:
        token = secrets.token_urlsafe(32)
        session_id = f"session-{uuid4().hex[:12]}"
        token_hash = _hash_token(token)
        now = time.time()
        expires_at = now + max(1, ttl_days) * 86400
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO user_sessions (
                    session_id, user_id, token_hash, status, created_at, updated_at, expires_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (session_id, user_id, token_hash, "active", _utcnow(), _utcnow(), expires_at, now),
            )
        return {"session_id": session_id, "token": token, "expires_at": expires_at}

    def get_session(self, token: str) -> dict[str, Any] | None:
        token_hash = _hash_token(token)
        now = time.time()
        with self._lock:
            row = self._conn.execute(
                """
                SELECT s.*, u.email, u.display_name, u.is_admin
                FROM user_sessions s
                JOIN users u ON u.user_id = s.user_id
                WHERE s.token_hash = ? AND s.status = 'active'
                """,
                (token_hash,),
            ).fetchone()
        if row is None or float(row["expires_at"] or 0.0) < now:
            return None
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE user_sessions SET last_seen_at = ?, updated_at = ? WHERE session_id = ?",
                (now, _utcnow(), row["session_id"]),
            )
        return {
            "session_id": row["session_id"],
            "user_id": row["user_id"],
            "email": row["email"],
            "display_name": row["display_name"] or "",
            "is_admin": bool(row["is_admin"]),
            "expires_at": float(row["expires_at"]),
            "last_seen_at": float(row["last_seen_at"] or now),
        }

    def revoke_session(self, token: str) -> None:
        token_hash = _hash_token(token)
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE user_sessions SET status = 'revoked', updated_at = ? WHERE token_hash = ?",
                (_utcnow(), token_hash),
            )

    def save_user_provider_secret(
        self,
        *,
        user_id: str,
        provider: str,
        payload: dict[str, Any],
    ) -> None:
        now = _utcnow()
        encrypted = self._fernet.encrypt(_json_dumps(payload).encode("utf-8"))
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO user_provider_secrets (
                    user_id, provider, encrypted_payload, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id, provider) DO UPDATE SET
                    encrypted_payload = excluded.encrypted_payload,
                    updated_at = excluded.updated_at
                """,
                (user_id, provider, encrypted, now, now),
            )

    def delete_user_provider_secret(self, *, user_id: str, provider: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM user_provider_secrets WHERE user_id = ? AND provider = ?",
                (user_id, provider),
            )

    def load_user_provider_secrets(self, user_id: str) -> dict[str, dict[str, str]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT provider, encrypted_payload FROM user_provider_secrets WHERE user_id = ?",
                (user_id,),
            ).fetchall()
        secrets_map: dict[str, dict[str, str]] = {}
        for row in rows:
            payload = json.loads(self._fernet.decrypt(row["encrypted_payload"]).decode("utf-8"))
            secrets_map[row["provider"]] = {str(k): str(v) for k, v in payload.items() if v not in (None, "")}
        return secrets_map

    def list_user_provider_secrets(self, user_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT provider, encrypted_payload, created_at, updated_at FROM user_provider_secrets WHERE user_id = ? ORDER BY provider ASC",
                (user_id,),
            ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            payload = json.loads(self._fernet.decrypt(row["encrypted_payload"]).decode("utf-8"))
            items.append(
                {
                    "provider": row["provider"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                    "fields": sorted(payload.keys()),
                    "preview": {
                        key: _mask_secret(str(value))
                        for key, value in payload.items()
                        if value not in (None, "")
                    },
                }
            )
        return items

    def list_users(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT u.*, COUNT(ups.provider) AS secret_count
                FROM users u
                LEFT JOIN user_provider_secrets ups ON ups.user_id = u.user_id
                GROUP BY u.user_id
                ORDER BY u.created_at ASC
                """
            ).fetchall()
        return [
            {
                "user_id": row["user_id"],
                "email": row["email"],
                "display_name": row["display_name"] or "",
                "is_admin": bool(row["is_admin"]),
                "secret_count": int(row["secret_count"] or 0),
                "created_at": row["created_at"],
                "last_login_at": row["last_login_at"],
            }
            for row in rows
        ]

    def admin_overview(self) -> dict[str, Any]:
        with self._lock:
            user_count = self._conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            session_count = self._conn.execute(
                "SELECT COUNT(*) FROM user_sessions WHERE status = 'active' AND expires_at >= ?",
                (time.time(),),
            ).fetchone()[0]
            secret_count = self._conn.execute("SELECT COUNT(*) FROM user_provider_secrets").fetchone()[0]
        return {
            "users": int(user_count or 0),
            "active_sessions": int(session_count or 0),
            "stored_provider_sets": int(secret_count or 0),
            "workers": len(self.list_workers()),
            "pending_approvals": len(self.list_approvals(status="pending")),
        }

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @property
    def path(self) -> Path:
        return self._path

    def _load_or_create_key(self) -> bytes:
        if self._key_path.exists():
            return self._key_path.read_bytes()
        key = Fernet.generate_key()
        self._key_path.write_bytes(key)
        return key

    def _init_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS mission_audits (
                    mission_id TEXT PRIMARY KEY,
                    audit_log_path TEXT NOT NULL,
                    request TEXT NOT NULL,
                    plan_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    step_results_json TEXT NOT NULL,
                    artifacts_json TEXT NOT NULL,
                    mission_trace_json TEXT NOT NULL,
                    resumed_from_step TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS critique_memory (
                    mission_id TEXT NOT NULL,
                    step_id TEXT NOT NULL,
                    skill_name TEXT NOT NULL,
                    notes_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (mission_id, step_id, skill_name)
                );

                CREATE TABLE IF NOT EXISTS approvals (
                    approval_id TEXT PRIMARY KEY,
                    mission_id TEXT NOT NULL,
                    step_id TEXT NOT NULL,
                    approval_class TEXT NOT NULL,
                    status TEXT NOT NULL,
                    request_excerpt TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    encrypted_payload BLOB NOT NULL,
                    policy_mode TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    expires_at TEXT,
                    approved_at TEXT,
                    rejected_at TEXT,
                    decision_notes TEXT DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS task_leases (
                    idempotency_key TEXT PRIMARY KEY,
                    service_name TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    mission_id TEXT NOT NULL,
                    step_id TEXT NOT NULL,
                    worker_id TEXT NOT NULL,
                    ticket_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    lease_expires_at REAL NOT NULL,
                    result_json TEXT,
                    error TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS workers (
                    worker_id TEXT PRIMARY KEY,
                    endpoint_url TEXT NOT NULL,
                    services_json TEXT NOT NULL,
                    capabilities_json TEXT NOT NULL,
                    process_mode TEXT NOT NULL,
                    lease_ttl_seconds INTEGER NOT NULL,
                    last_seen_at REAL NOT NULL,
                    metrics_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL DEFAULT '',
                    password_hash TEXT NOT NULL,
                    password_salt TEXT NOT NULL,
                    is_admin INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_login_at TEXT
                );

                CREATE TABLE IF NOT EXISTS user_sessions (
                    session_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    last_seen_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS user_provider_secrets (
                    user_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    encrypted_payload BLOB NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, provider)
                );
                """
            )
