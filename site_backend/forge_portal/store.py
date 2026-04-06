from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from cryptography.fernet import Fernet

PROVIDER_REQUIREMENTS: dict[str, set[str]] = {
    "cloudflare": {"api_key"},
    "nvidia": {"api_key"},
    "openai": {"api_key"},
    "anthropic": {"api_key"},
    "groq": {"api_key"},
    "gemini": {"api_key"},
    "deepseek": {"api_key"},
    "openrouter": {"api_key"},
    "mistral": {"api_key"},
    "together": {"api_key"},
    "ollama": {"api_key"},
}

TOKEN_TTL_HOURS = {"verify_email": 48, "password_reset": 2}


def _utcnow() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


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


def _parse_iso_timestamp(value: str | None) -> float:
    if not value:
        return 0.0
    from datetime import datetime

    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


@dataclass
class PortalUser:
    user_id: str
    email: str
    display_name: str
    is_admin: bool
    email_verified: bool
    created_at: str
    updated_at: str
    last_login_at: str | None = None
    verified_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "email": self.email,
            "display_name": self.display_name,
            "is_admin": self.is_admin,
            "email_verified": self.email_verified,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_login_at": self.last_login_at,
            "verified_at": self.verified_at,
        }


class PortalStateStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._db_path = self.root / "portal.sqlite3"
        self._key_path = self.root / "portal.key"
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._fernet = Fernet(self._load_or_create_key())
        self._init_schema()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def create_user(
        self,
        *,
        email: str,
        password: str,
        display_name: str = "",
        manager_email: str = "",
    ) -> PortalUser:
        normalized_email = email.strip().lower()
        if not normalized_email or "@" not in normalized_email:
            raise ValueError("Valid email is required.")
        if len(password) < 8:
            raise ValueError("Password must be at least 8 characters.")
        if len(display_name.strip()) > 120:
            raise ValueError("Display name is too long.")
        user_id = f"user-{uuid4().hex[:12]}"
        salt = secrets.token_bytes(16)
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
                    is_admin, email_verified, verified_at, created_at, updated_at, last_login_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    normalized_email,
                    display_name.strip(),
                    _hash_password(password, salt),
                    salt.hex(),
                    1 if normalized_email == manager_email.strip().lower() else 0,
                    0,
                    None,
                    now,
                    now,
                    None,
                ),
            )
        return self.get_user(user_id)

    def get_user(self, user_id: str) -> PortalUser:
        with self._lock:
            row = self._conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if row is None:
            raise ValueError("User not found.")
        return self._row_to_user(row)

    def find_user_by_email(self, email: str) -> PortalUser | None:
        normalized_email = email.strip().lower()
        with self._lock:
            row = self._conn.execute("SELECT * FROM users WHERE email = ?", (normalized_email,)).fetchone()
        return self._row_to_user(row) if row is not None else None

    def upsert_google_user(
        self,
        *,
        email: str,
        display_name: str = "",
        manager_email: str = "",
        email_verified: bool = True,
    ) -> PortalUser:
        normalized_email = email.strip().lower()
        if not normalized_email or "@" not in normalized_email:
            raise ValueError("Valid email is required.")
        clean_display_name = display_name.strip()
        now = _utcnow()
        with self._lock:
            row = self._conn.execute("SELECT * FROM users WHERE email = ?", (normalized_email,)).fetchone()
        if row is None:
            random_password = secrets.token_urlsafe(24)
            created = self.create_user(
                email=normalized_email,
                password=random_password,
                display_name=clean_display_name,
                manager_email=manager_email,
            )
            if email_verified:
                with self._lock, self._conn:
                    self._conn.execute(
                        "UPDATE users SET email_verified = 1, verified_at = ?, updated_at = ? WHERE user_id = ?",
                        (now, now, created.user_id),
                    )
                return self.get_user(created.user_id)
            return created

        updates: list[str] = []
        values: list[Any] = []
        if clean_display_name and clean_display_name != (row["display_name"] or ""):
            updates.append("display_name = ?")
            values.append(clean_display_name)
        if email_verified and not bool(row["email_verified"]):
            updates.append("email_verified = 1")
            updates.append("verified_at = ?")
            values.append(now)
        if normalized_email == manager_email.strip().lower() and not bool(row["is_admin"]):
            updates.append("is_admin = 1")
        if updates:
            updates.append("updated_at = ?")
            values.append(now)
            values.append(row["user_id"])
            with self._lock, self._conn:
                self._conn.execute(
                    f"UPDATE users SET {', '.join(updates)} WHERE user_id = ?",
                    tuple(values),
                )
        return self.get_user(row["user_id"])

    def authenticate_user(self, *, email: str, password: str) -> PortalUser | None:
        normalized_email = email.strip().lower()
        with self._lock:
            row = self._conn.execute("SELECT * FROM users WHERE email = ?", (normalized_email,)).fetchone()
        if row is None:
            return None
        salt = bytes.fromhex(row["password_salt"])
        candidate = _hash_password(password, salt)
        if not hmac.compare_digest(candidate, row["password_hash"]):
            return None
        now = _utcnow()
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE users SET last_login_at = ?, updated_at = ? WHERE user_id = ?",
                (now, now, row["user_id"]),
            )
        return self.get_user(row["user_id"])

    def create_session(self, *, user_id: str, ttl_days: int = 30) -> dict[str, Any]:
        token = secrets.token_urlsafe(32)
        now = time.time()
        expires_at = now + max(1, ttl_days) * 86400
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO user_sessions (
                    session_id, user_id, token_hash, status, created_at, updated_at, expires_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"session-{uuid4().hex[:12]}",
                    user_id,
                    _hash_token(token),
                    "active",
                    _utcnow(),
                    _utcnow(),
                    expires_at,
                    now,
                ),
            )
        return {"token": token, "expires_at": expires_at}

    def get_session(self, token: str) -> dict[str, Any] | None:
        now = time.time()
        with self._lock:
            row = self._conn.execute(
                """
                SELECT s.*, u.email, u.display_name, u.is_admin, u.email_verified, u.verified_at
                FROM user_sessions s
                JOIN users u ON u.user_id = s.user_id
                WHERE s.token_hash = ? AND s.status = 'active'
                """,
                (_hash_token(token),),
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
            "email_verified": bool(row["email_verified"]),
            "verified_at": row["verified_at"],
            "expires_at": float(row["expires_at"]),
            "last_seen_at": float(row["last_seen_at"] or now),
        }

    def revoke_session(self, token: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE user_sessions SET status = 'revoked', updated_at = ? WHERE token_hash = ?",
                (_utcnow(), _hash_token(token)),
            )

    def save_user_provider_secret(self, *, user_id: str, provider: str, payload: dict[str, Any]) -> None:
        now = _utcnow()
        encrypted = self._fernet.encrypt(_json_dumps(payload).encode("utf-8"))
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO user_provider_secrets (user_id, provider, encrypted_payload, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
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

    def list_user_provider_secrets(self, user_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT provider, encrypted_payload, created_at, updated_at
                FROM user_provider_secrets
                WHERE user_id = ?
                ORDER BY provider ASC
                """,
                (user_id,),
            ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            payload = json.loads(self._fernet.decrypt(row["encrypted_payload"]).decode("utf-8"))
            required = PROVIDER_REQUIREMENTS.get(row["provider"], {"api_key"})
            present = {key for key, value in payload.items() if value not in (None, "")}
            missing = sorted(required.difference(present))
            stale = _parse_iso_timestamp(row["updated_at"]) < (time.time() - 90 * 86400)
            health = "ready"
            if missing:
                health = "incomplete"
            elif stale:
                health = "stale"
            items.append(
                {
                    "provider": row["provider"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                    "fields": sorted(payload.keys()),
                    "required_fields": sorted(required),
                    "missing_fields": missing,
                    "health": health,
                    "preview": {
                        key: _mask_secret(str(value))
                        for key, value in payload.items()
                        if value not in (None, "")
                    },
                }
            )
        return items

    def export_user_provider_secrets(self, user_id: str) -> dict[str, dict[str, str]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT provider, encrypted_payload FROM user_provider_secrets WHERE user_id = ?",
                (user_id,),
            ).fetchall()
        result: dict[str, dict[str, str]] = {}
        for row in rows:
            payload = json.loads(self._fernet.decrypt(row["encrypted_payload"]).decode("utf-8"))
            result[row["provider"]] = {str(k): str(v) for k, v in payload.items() if v not in (None, "")}
        return result

    def list_user_key_health(self) -> list[dict[str, Any]]:
        with self._lock:
            users = self._conn.execute(
                "SELECT user_id, email, display_name FROM users ORDER BY created_at ASC"
            ).fetchall()
        items: list[dict[str, Any]] = []
        for user in users:
            saved = self.list_user_provider_secrets(user["user_id"])
            items.append(
                {
                    "user_id": user["user_id"],
                    "email": user["email"],
                    "display_name": user["display_name"] or "",
                    "configured_providers": len(saved),
                    "healthy_providers": sum(1 for item in saved if item["health"] == "ready"),
                    "incomplete_providers": sum(1 for item in saved if item["health"] == "incomplete"),
                    "stale_providers": sum(1 for item in saved if item["health"] == "stale"),
                    "providers": saved,
                }
            )
        return items

    def list_users(self) -> list[dict[str, Any]]:
        health_by_user = {item["user_id"]: item for item in self.list_user_key_health()}
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
        items: list[dict[str, Any]] = []
        for row in rows:
            health = health_by_user.get(row["user_id"], {})
            items.append(
                {
                    "user_id": row["user_id"],
                    "email": row["email"],
                    "display_name": row["display_name"] or "",
                    "is_admin": bool(row["is_admin"]),
                    "email_verified": bool(row["email_verified"]),
                    "verified_at": row["verified_at"],
                    "secret_count": int(row["secret_count"] or 0),
                    "created_at": row["created_at"],
                    "last_login_at": row["last_login_at"],
                    "healthy_provider_sets": int(health.get("healthy_providers", 0)),
                    "incomplete_provider_sets": int(health.get("incomplete_providers", 0)),
                    "stale_provider_sets": int(health.get("stale_providers", 0)),
                }
            )
        return items

    def create_auth_token(
        self,
        *,
        user_id: str,
        kind: str,
        ttl_hours: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        raw_token = secrets.token_urlsafe(32)
        token_id = f"token-{uuid4().hex[:12]}"
        expires_at = time.time() + max(1, ttl_hours or TOKEN_TTL_HOURS.get(kind, 24)) * 3600
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO auth_tokens (
                    token_id, user_id, kind, token_hash, expires_at,
                    consumed_at, created_at, updated_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    token_id,
                    user_id,
                    kind,
                    _hash_token(raw_token),
                    expires_at,
                    None,
                    _utcnow(),
                    _utcnow(),
                    _json_dumps(metadata or {}),
                ),
            )
        return {"token_id": token_id, "raw_token": raw_token, "expires_at": expires_at}

    def consume_auth_token(self, *, token: str, kind: str) -> dict[str, Any] | None:
        row = self._consume_auth_token(token=token, kind=kind)
        if row is None:
            return None
        metadata_raw = row["metadata_json"] or "{}"
        try:
            metadata = json.loads(metadata_raw)
        except json.JSONDecodeError:
            metadata = {}
        return {
            "token_id": row["token_id"],
            "user_id": row["user_id"],
            "kind": row["kind"],
            "expires_at": float(row["expires_at"] or 0.0),
            "metadata": metadata if isinstance(metadata, dict) else {},
        }

    def request_email_verification(
        self,
        *,
        user_id: str,
        app_base_url: str,
        debug_token: bool = False,
    ) -> dict[str, Any]:
        user = self.get_user(user_id)
        if user.email_verified:
            return {"delivery_mode": "already_verified", "email": user.email, "expires_at": None}
        issued = self.create_auth_token(user_id=user_id, kind="verify_email")
        link = f"{app_base_url.rstrip('/')}/?verify_token={issued['raw_token']}"
        outbox_id = self.enqueue_outbox_message(
            user_id=user_id,
            email=user.email,
            kind="verify_email",
            subject="Verify your FORGE email",
            body=f"Verify your FORGE email.\n\n{link}\n\nToken: {issued['raw_token']}",
            token_id=issued["token_id"],
        )
        payload = {
            "delivery_mode": "outbox",
            "email": user.email,
            "outbox_id": outbox_id,
            "expires_at": issued["expires_at"],
        }
        if debug_token:
            payload["debug_token"] = issued["raw_token"]
        return payload

    def verify_email(self, *, token: str) -> PortalUser:
        row = self._consume_auth_token(token=token, kind="verify_email")
        if row is None:
            raise ValueError("Verification token is invalid or expired.")
        verified_at = _utcnow()
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE users SET email_verified = 1, verified_at = ?, updated_at = ? WHERE user_id = ?",
                (verified_at, verified_at, row["user_id"]),
            )
        return self.get_user(row["user_id"])

    def request_password_reset(
        self,
        *,
        email: str,
        app_base_url: str,
        debug_token: bool = False,
    ) -> dict[str, Any]:
        user = self.find_user_by_email(email)
        if user is None:
            return {"accepted": True, "delivery_mode": "silent", "email": email.strip().lower()}
        issued = self.create_auth_token(user_id=user.user_id, kind="password_reset")
        link = f"{app_base_url.rstrip('/')}/?reset_token={issued['raw_token']}"
        outbox_id = self.enqueue_outbox_message(
            user_id=user.user_id,
            email=user.email,
            kind="password_reset",
            subject="Reset your FORGE password",
            body=f"Reset your FORGE password.\n\n{link}\n\nToken: {issued['raw_token']}",
            token_id=issued["token_id"],
        )
        payload = {
            "accepted": True,
            "delivery_mode": "outbox",
            "email": user.email,
            "outbox_id": outbox_id,
            "expires_at": issued["expires_at"],
        }
        if debug_token:
            payload["debug_token"] = issued["raw_token"]
        return payload

    def reset_password(self, *, token: str, new_password: str) -> PortalUser:
        if len(new_password) < 8:
            raise ValueError("Password must be at least 8 characters.")
        row = self._consume_auth_token(token=token, kind="password_reset")
        if row is None:
            raise ValueError("Reset token is invalid or expired.")
        salt = secrets.token_bytes(16)
        now = _utcnow()
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE users
                SET password_hash = ?, password_salt = ?, updated_at = ?
                WHERE user_id = ?
                """,
                (_hash_password(new_password, salt), salt.hex(), now, row["user_id"]),
            )
        return self.get_user(row["user_id"])

    def create_device_login(
        self,
        *,
        app_base_url: str,
        display_name: str = "",
        mode: str = "browser",
        ttl_hours: int = 1,
    ) -> dict[str, Any]:
        normalized_mode = mode.strip().lower() or "browser"
        if normalized_mode not in {"browser", "google"}:
            normalized_mode = "browser"
        issued = self.create_auth_token(
            user_id="desktop-device",
            kind="desktop_device_login",
            ttl_hours=ttl_hours,
            metadata={
                "status": "pending",
                "mode": normalized_mode,
                "display_name": display_name.strip(),
                "app_base_url": app_base_url.rstrip("/"),
            },
        )
        return {
            "device_code": issued["raw_token"],
            "verification_url": (
                f"{app_base_url.rstrip('/')}/?device_code={issued['raw_token']}"
                f"&from=desktop&mode={normalized_mode}"
            ),
            "expires_at": issued["expires_at"],
            "interval_seconds": 2,
        }

    def complete_device_login(self, *, token: str, user_id: str) -> dict[str, Any]:
        row = self._get_auth_token(token=token, kind="desktop_device_login")
        if row is None:
            raise ValueError("Desktop sign-in token is invalid or expired.")
        metadata = self._parse_token_metadata(row["metadata_json"])
        metadata["status"] = "approved"
        metadata["approved_user_id"] = user_id
        metadata["approved_at"] = _utcnow()
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE auth_tokens SET user_id = ?, metadata_json = ?, updated_at = ? WHERE token_id = ?",
                (user_id, _json_dumps(metadata), _utcnow(), row["token_id"]),
            )
        return {
            "status": "approved",
            "approved_user_id": user_id,
            "expires_at": float(row["expires_at"] or 0.0),
        }

    def get_device_login_status(self, *, token: str, ttl_days: int = 30) -> dict[str, Any]:
        row = self._get_auth_token(token=token, kind="desktop_device_login")
        if row is None:
            return {"status": "expired"}
        metadata = self._parse_token_metadata(row["metadata_json"])
        status = str(metadata.get("status", "pending")).strip() or "pending"
        if status != "approved":
            return {
                "status": status,
                "expires_at": float(row["expires_at"] or 0.0),
            }
        session = self.create_session(user_id=row["user_id"], ttl_days=ttl_days)
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE auth_tokens SET consumed_at = ?, updated_at = ? WHERE token_id = ?",
                (_utcnow(), _utcnow(), row["token_id"]),
            )
        return {
            "status": "approved",
            "session_token": session["token"],
            "user": self.get_user(row["user_id"]).to_dict(),
        }

    def enqueue_outbox_message(
        self,
        *,
        user_id: str | None,
        email: str,
        kind: str,
        subject: str,
        body: str,
        token_id: str | None = None,
    ) -> str:
        outbox_id = f"outbox-{uuid4().hex[:12]}"
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO outbox_messages (
                    outbox_id, user_id, email, kind, subject, body,
                    token_id, delivery_mode, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (outbox_id, user_id, email.strip().lower(), kind, subject, body, token_id, "outbox", _utcnow()),
            )
        return outbox_id

    def list_outbox_messages(self, *, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT outbox_id, user_id, email, kind, subject, token_id, delivery_mode, created_at
                FROM outbox_messages
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (max(1, limit),),
            ).fetchall()
        return [
            {
                "outbox_id": row["outbox_id"],
                "user_id": row["user_id"],
                "email": row["email"],
                "kind": row["kind"],
                "subject": row["subject"],
                "token_id": row["token_id"],
                "delivery_mode": row["delivery_mode"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def upsert_mission_event(
        self,
        *,
        user_id: str,
        mission_id: str,
        objective: str,
        status: str,
        validation_status: str,
        summary: str,
        workspace_root: str,
        source: str = "desktop",
    ) -> None:
        now = _utcnow()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO mission_events (
                    mission_id, user_id, objective, status, validation_status,
                    summary, workspace_root, source, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(mission_id) DO UPDATE SET
                    user_id = excluded.user_id,
                    objective = excluded.objective,
                    status = excluded.status,
                    validation_status = excluded.validation_status,
                    summary = excluded.summary,
                    workspace_root = excluded.workspace_root,
                    source = excluded.source,
                    updated_at = excluded.updated_at
                """,
                (
                    mission_id,
                    user_id,
                    objective,
                    status,
                    validation_status,
                    summary,
                    workspace_root,
                    source,
                    now,
                    now,
                ),
            )

    def list_missions(self, *, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT m.*, u.email
                FROM mission_events m
                JOIN users u ON u.user_id = m.user_id
                ORDER BY m.updated_at DESC
                LIMIT ?
                """,
                (max(1, limit),),
            ).fetchall()
        return [
            {
                "mission_id": row["mission_id"],
                "user_id": row["user_id"],
                "email": row["email"],
                "objective": row["objective"],
                "status": row["status"],
                "validation_status": row["validation_status"],
                "summary": row["summary"],
                "workspace_root": row["workspace_root"],
                "source": row["source"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def upsert_approval_event(
        self,
        *,
        user_id: str,
        approval_id: str,
        mission_id: str,
        step_id: str,
        approval_class: str,
        status: str,
        summary: str,
        request_excerpt: str,
        source: str = "desktop",
    ) -> None:
        now = _utcnow()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO approval_events (
                    approval_id, mission_id, user_id, step_id, approval_class, status,
                    summary, request_excerpt, source, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(approval_id) DO UPDATE SET
                    mission_id = excluded.mission_id,
                    user_id = excluded.user_id,
                    step_id = excluded.step_id,
                    approval_class = excluded.approval_class,
                    status = excluded.status,
                    summary = excluded.summary,
                    request_excerpt = excluded.request_excerpt,
                    source = excluded.source,
                    updated_at = excluded.updated_at
                """,
                (
                    approval_id,
                    mission_id,
                    user_id,
                    step_id,
                    approval_class,
                    status,
                    summary,
                    request_excerpt,
                    source,
                    now,
                    now,
                ),
            )

    def list_approval_events(self, *, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT a.*, u.email
                FROM approval_events a
                JOIN users u ON u.user_id = a.user_id
                ORDER BY a.updated_at DESC
                LIMIT ?
                """,
                (max(1, limit),),
            ).fetchall()
        return [
            {
                "approval_id": row["approval_id"],
                "mission_id": row["mission_id"],
                "user_id": row["user_id"],
                "email": row["email"],
                "step_id": row["step_id"],
                "approval_class": row["approval_class"],
                "status": row["status"],
                "summary": row["summary"],
                "request_excerpt": row["request_excerpt"],
                "source": row["source"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def admin_overview(self) -> dict[str, Any]:
        with self._lock:
            user_count = self._conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            verified_users = self._conn.execute("SELECT COUNT(*) FROM users WHERE email_verified = 1").fetchone()[0]
            session_count = self._conn.execute(
                "SELECT COUNT(*) FROM user_sessions WHERE status = 'active' AND expires_at >= ?",
                (time.time(),),
            ).fetchone()[0]
            secret_count = self._conn.execute("SELECT COUNT(*) FROM user_provider_secrets").fetchone()[0]
            mission_count = self._conn.execute("SELECT COUNT(*) FROM mission_events").fetchone()[0]
            approval_count = self._conn.execute(
                "SELECT COUNT(*) FROM approval_events WHERE status = 'pending'"
            ).fetchone()[0]
            outbox_count = self._conn.execute("SELECT COUNT(*) FROM outbox_messages").fetchone()[0]
        return {
            "users": int(user_count or 0),
            "verified_users": int(verified_users or 0),
            "active_sessions": int(session_count or 0),
            "stored_provider_sets": int(secret_count or 0),
            "missions": int(mission_count or 0),
            "pending_approvals": int(approval_count or 0),
            "outbox_messages": int(outbox_count or 0),
        }

    def _row_to_user(self, row: sqlite3.Row) -> PortalUser:
        return PortalUser(
            user_id=row["user_id"],
            email=row["email"],
            display_name=row["display_name"] or "",
            is_admin=bool(row["is_admin"]),
            email_verified=bool(row["email_verified"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_login_at=row["last_login_at"],
            verified_at=row["verified_at"],
        )

    def _consume_auth_token(self, *, token: str, kind: str) -> sqlite3.Row | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT *
                FROM auth_tokens
                WHERE token_hash = ? AND kind = ? AND consumed_at IS NULL
                """,
                (_hash_token(token.strip()), kind),
            ).fetchone()
        if row is None or float(row["expires_at"] or 0.0) < time.time():
            return None
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE auth_tokens SET consumed_at = ?, updated_at = ? WHERE token_id = ?",
                (_utcnow(), _utcnow(), row["token_id"]),
            )
        return row

    def _get_auth_token(self, *, token: str, kind: str) -> sqlite3.Row | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT *
                FROM auth_tokens
                WHERE token_hash = ? AND kind = ? AND consumed_at IS NULL
                """,
                (_hash_token(token.strip()), kind),
            ).fetchone()
        if row is None or float(row["expires_at"] or 0.0) < time.time():
            return None
        return row

    @staticmethod
    def _parse_token_metadata(raw: str | None) -> dict[str, Any]:
        if not raw:
            return {}
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _load_or_create_key(self) -> bytes:
        if self._key_path.exists():
            return self._key_path.read_bytes()
        key = Fernet.generate_key()
        self._key_path.write_bytes(key)
        return key

    def _ensure_column(self, table_name: str, column_name: str, ddl: str) -> None:
        with self._lock:
            rows = self._conn.execute(f"PRAGMA table_info({table_name})").fetchall()
            columns = {row["name"] for row in rows}
        if column_name in columns:
            return
        with self._lock, self._conn:
            self._conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {ddl}")

    def _init_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL DEFAULT '',
                    password_hash TEXT NOT NULL,
                    password_salt TEXT NOT NULL,
                    is_admin INTEGER NOT NULL DEFAULT 0,
                    email_verified INTEGER NOT NULL DEFAULT 0,
                    verified_at TEXT,
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

                CREATE TABLE IF NOT EXISTS auth_tokens (
                    token_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,
                    expires_at REAL NOT NULL,
                    consumed_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS outbox_messages (
                    outbox_id TEXT PRIMARY KEY,
                    user_id TEXT,
                    email TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    body TEXT NOT NULL,
                    token_id TEXT,
                    delivery_mode TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS mission_events (
                    mission_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    status TEXT NOT NULL,
                    validation_status TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    workspace_root TEXT NOT NULL,
                    source TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS approval_events (
                    approval_id TEXT PRIMARY KEY,
                    mission_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    step_id TEXT NOT NULL,
                    approval_class TEXT NOT NULL,
                    status TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    request_excerpt TEXT NOT NULL,
                    source TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
        self._ensure_column("users", "email_verified", "email_verified INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("users", "verified_at", "verified_at TEXT")
        self._ensure_column("auth_tokens", "metadata_json", "metadata_json TEXT NOT NULL DEFAULT '{}'")
