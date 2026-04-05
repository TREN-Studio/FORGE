from __future__ import annotations

import json
from dataclasses import dataclass
from http import HTTPStatus
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

try:
    from .store import PortalStateStore
except ImportError:  # pragma: no cover - deployed flat backend fallback
    from store import PortalStateStore


SESSION_COOKIE = "forge_portal_session"
SUPPORTED_PROVIDERS = [
    "cloudflare",
    "nvidia",
    "openai",
    "anthropic",
    "groq",
    "gemini",
    "deepseek",
    "openrouter",
    "mistral",
    "together",
    "ollama",
]
PROVIDER_FIELDS: dict[str, list[str]] = {
    "cloudflare": ["api_key", "account_id", "global_key", "email"],
    "openai": ["api_key", "organization", "project"],
    "anthropic": ["api_key"],
    "nvidia": ["api_key"],
    "groq": ["api_key"],
    "gemini": ["api_key"],
    "deepseek": ["api_key"],
    "openrouter": ["api_key"],
    "mistral": ["api_key"],
    "together": ["api_key"],
    "ollama": ["api_key"],
}
ALLOWED_SECRET_FIELDS = {"api_key", "account_id", "organization", "project", "global_key", "email"}


@dataclass
class PortalConfig:
    state_root: Path
    manager_email: str = "larbilife@gmail.com"
    cookie_path: str = "/"
    auth_session_days: int = 30


@dataclass
class PortalResponse:
    status: int
    headers: dict[str, str]
    body: str

    def to_http(self) -> tuple[int, dict[str, str], bytes]:
        return self.status, self.headers, self.body.encode("utf-8")


def json_response(
    payload: dict[str, Any],
    *,
    status: HTTPStatus = HTTPStatus.OK,
    headers: dict[str, str] | None = None,
) -> PortalResponse:
    body = json.dumps(payload, ensure_ascii=False)
    merged = {"Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store"}
    if headers:
        merged.update(headers)
    return PortalResponse(status=int(status), headers=merged, body=body)


def _load_json(body: bytes) -> dict[str, Any]:
    if not body:
        return {}
    try:
        return json.loads(body.decode("utf-8"))
    except json.JSONDecodeError:
        return {}


def _read_session_token(headers: dict[str, str]) -> str | None:
    raw = headers.get("cookie", "").strip()
    if not raw:
        return None
    cookie = SimpleCookie()
    cookie.load(raw)
    morsel = cookie.get(SESSION_COOKIE)
    if morsel is None:
        return None
    value = morsel.value.strip()
    return value or None


def _cookie_header(token: str, *, path: str, expire: bool = False) -> str:
    parts = [f"{SESSION_COOKIE}={token}", f"Path={path}", "HttpOnly", "SameSite=Lax"]
    if expire:
        parts.append("Max-Age=0")
    return "; ".join(parts)


def _normalize_route(path: str) -> str:
    clean = path or "/"
    if clean.startswith("/api"):
        clean = clean[4:] or "/"
    if not clean.startswith("/"):
        clean = "/" + clean
    if clean != "/" and clean.endswith("/"):
        clean = clean[:-1]
    return clean


def _get_current_user(store: PortalStateStore, headers: dict[str, str]) -> dict[str, Any] | None:
    token = _read_session_token(headers)
    if not token:
        return None
    return store.get_session(token)


def _require_user(store: PortalStateStore, headers: dict[str, str]) -> dict[str, Any]:
    user = _get_current_user(store, headers)
    if user is None:
        raise PermissionError("Login required.")
    return user


def _require_admin(store: PortalStateStore, headers: dict[str, str]) -> dict[str, Any]:
    user = _require_user(store, headers)
    if not bool(user.get("is_admin")):
        raise PermissionError("Admin access required.")
    return user


def _provider_catalog() -> list[dict[str, Any]]:
    return [
        {"name": name, "fields": PROVIDER_FIELDS.get(name, ["api_key"])}
        for name in SUPPORTED_PROVIDERS
    ]


def handle_request(
    config: PortalConfig,
    store: PortalStateStore,
    *,
    method: str,
    path: str,
    query_string: str = "",
    headers: dict[str, str] | None = None,
    body: bytes = b"",
) -> PortalResponse:
    headers = {str(key).lower(): str(value) for key, value in (headers or {}).items()}
    route = _normalize_route(path)
    payload = _load_json(body)
    _ = parse_qs(query_string, keep_blank_values=True)

    try:
        if method == "GET" and route == "/health":
            return json_response({"ok": True, "manager_email": config.manager_email})

        if method == "GET" and route == "/auth/me":
            user = _get_current_user(store, headers)
            if user is None:
                return json_response({"authenticated": False, "manager_email": config.manager_email})
            return json_response(
                {
                    "authenticated": True,
                    "user": user,
                    "manager_email": config.manager_email,
                }
            )

        if method == "POST" and route == "/auth/register":
            user = store.create_user(
                email=str(payload.get("email", "")).strip(),
                password=str(payload.get("password", "")),
                display_name=str(payload.get("display_name", "")).strip(),
                manager_email=config.manager_email,
            )
            session = store.create_session(user_id=user.user_id, ttl_days=config.auth_session_days)
            return json_response(
                {"authenticated": True, "user": user.to_dict()},
                headers={"Set-Cookie": _cookie_header(session["token"], path=config.cookie_path)},
            )

        if method == "POST" and route == "/auth/login":
            user = store.authenticate_user(
                email=str(payload.get("email", "")).strip(),
                password=str(payload.get("password", "")),
            )
            if user is None:
                return json_response(
                    {"error": "Invalid email or password."},
                    status=HTTPStatus.UNAUTHORIZED,
                )
            session = store.create_session(user_id=user.user_id, ttl_days=config.auth_session_days)
            return json_response(
                {"authenticated": True, "user": user.to_dict()},
                headers={"Set-Cookie": _cookie_header(session["token"], path=config.cookie_path)},
            )

        if method == "POST" and route == "/auth/logout":
            token = _read_session_token(headers)
            if token:
                store.revoke_session(token)
            return json_response(
                {"authenticated": False},
                headers={"Set-Cookie": _cookie_header("", path=config.cookie_path, expire=True)},
            )

        if method == "GET" and route == "/user/keys":
            user = _require_user(store, headers)
            return json_response(
                {
                    "providers": _provider_catalog(),
                    "saved": store.list_user_provider_secrets(str(user["user_id"])),
                    "viewer": user,
                }
            )

        if method == "POST" and route == "/user/keys":
            user = _require_user(store, headers)
            provider = str(payload.get("provider", "")).strip().lower()
            if provider not in SUPPORTED_PROVIDERS:
                return json_response({"error": "Unsupported provider."}, status=HTTPStatus.BAD_REQUEST)
            secret_payload = {
                key: str(value).strip()
                for key, value in payload.items()
                if key in ALLOWED_SECRET_FIELDS and str(value).strip()
            }
            if secret_payload:
                store.save_user_provider_secret(
                    user_id=str(user["user_id"]),
                    provider=provider,
                    payload=secret_payload,
                )
            else:
                store.delete_user_provider_secret(user_id=str(user["user_id"]), provider=provider)
            return json_response(
                {
                    "providers": _provider_catalog(),
                    "saved": store.list_user_provider_secrets(str(user["user_id"])),
                    "viewer": user,
                }
            )

        if method == "GET" and route == "/admin/overview":
            user = _require_admin(store, headers)
            return json_response(
                {
                    "viewer": user,
                    "manager_email": config.manager_email,
                    "overview": store.admin_overview(),
                }
            )

        if method == "GET" and route == "/admin/users":
            _require_admin(store, headers)
            return json_response({"users": store.list_users()})

        return json_response({"error": "Not found."}, status=HTTPStatus.NOT_FOUND)
    except PermissionError as exc:
        return json_response({"error": str(exc)}, status=HTTPStatus.FORBIDDEN if "Admin" in str(exc) else HTTPStatus.UNAUTHORIZED)
    except ValueError as exc:
        return json_response({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
    except Exception as exc:  # noqa: BLE001
        return json_response(
            {
                "error": "Portal request failed.",
                "details": str(exc),
            },
            status=HTTPStatus.INTERNAL_SERVER_ERROR,
        )
