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


@dataclass
class PortalUser:
    user_id: str
    email: str
    display_name: str
    is_admin: bool
    created_at: str
    updated_at: str
    last_login_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "email": self.email,
            "display_name": self.display_name,
            "is_admin": self.is_admin,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_login_at": self.last_login_at,
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
        password_hash = _hash_password(password, salt)
        is_admin = normalized_email == manager_email.strip().lower()
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
        return self.get_user(user_id)

    def get_user(self, user_id: str) -> PortalUser:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        if row is None:
            raise ValueError("User not found.")
        return PortalUser(
            user_id=row["user_id"],
            email=row["email"],
            display_name=row["display_name"] or "",
            is_admin=bool(row["is_admin"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_login_at=row["last_login_at"],
        )

    def authenticate_user(self, *, email: str, password: str) -> PortalUser | None:
        normalized_email = email.strip().lower()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM users WHERE email = ?",
                (normalized_email,),
            ).fetchone()
        if row is None:
            return None
        salt = bytes.fromhex(row["password_salt"])
        candidate = _hash_password(password, salt)
        if not hmac.compare_digest(candidate, row["password_hash"]):
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
                    session_id, user_id, token_hash, status,
                    created_at, updated_at, expires_at, last_seen_at
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
            "workers": 0,
            "pending_approvals": 0,
        }

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
