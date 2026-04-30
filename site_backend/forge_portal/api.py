from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from http import HTTPStatus
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any
from urllib import error, request
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

try:
    from .store import PROVIDER_REQUIREMENTS, PortalStateStore
except ImportError:  # pragma: no cover
    from store import PROVIDER_REQUIREMENTS, PortalStateStore

try:
    from forge.providers.base import normalize_secret_value
except ModuleNotFoundError as exc:  # pragma: no cover - production portal is deployed standalone
    if exc.name != "forge":
        raise

    SECRET_TOKEN_PATTERNS: dict[str, tuple[str, ...]] = {
        "api_key": (
            r"(nvapi-[A-Za-z0-9._-]+)",
            r"(sk-proj-[A-Za-z0-9._-]+)",
            r"(sk-[A-Za-z0-9._-]+)",
            r"(gsk_[A-Za-z0-9._-]+)",
            r"(AIza[0-9A-Za-z_-]{20,})",
            r"(hf_[A-Za-z0-9]{20,})",
        ),
        "global_key": (
            r"([A-Fa-f0-9]{32,64})",
        ),
        "account_id": (
            r"([A-Fa-f0-9]{32})",
        ),
        "email": (
            r"([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})",
        ),
    }

    def normalize_secret_value(name: str, value: str | None) -> str | None:
        text = (value or "").strip()
        if not text:
            return None

        if name in {"api_key", "global_key"} and text.lower().startswith("bearer "):
            text = text.split(None, 1)[1].strip()

        patterns = SECRET_TOKEN_PATTERNS.get(name, ())
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                return match.group(1).strip()

        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if len(lines) <= 1:
            return text

        compact_candidates = [
            line
            for line in lines
            if " " not in line
            and not line.endswith(":")
            and len(line) >= 12
            and not re.fullmatch(r"[A-Za-z][A-Za-z0-9 _-]{0,40}", line)
        ]
        if compact_candidates:
            return compact_candidates[-1]
        return lines[-1]


SESSION_COOKIE = "forge_portal_session"
SUPPORTED_PROVIDERS = sorted(PROVIDER_REQUIREMENTS.keys())
SECURITY_HEADERS: dict[str, str] = {
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "Content-Security-Policy": (
        "default-src 'none'; "
        "base-uri 'none'; "
        "frame-ancestors 'none'; "
        "form-action 'none'; "
        "object-src 'none'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "script-src 'none'; "
        "style-src 'none'"
    ),
}
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
    app_base_url: str = "https://www.trenstudio.com/FORGE/portal"
    debug_auth_tokens: bool = False
    google_client_id: str = ""
    google_client_secret: str = ""
    google_authorize_url: str = "https://accounts.google.com/o/oauth2/v2/auth"
    google_token_url: str = "https://oauth2.googleapis.com/token"
    google_userinfo_url: str = "https://openidconnect.googleapis.com/v1/userinfo"
    google_tokeninfo_url: str = "https://oauth2.googleapis.com/tokeninfo"
    google_bridge_url: str = ""

    @property
    def google_oauth_enabled(self) -> bool:
        return bool(self.google_client_id)

    @property
    def google_oauth_mode(self) -> str:
        if self.google_client_id and self.google_client_secret:
            return "oauth_code"
        if self.google_client_id and self.google_bridge_url:
            return "bridge_id_token"
        if self.google_client_id:
            return "id_token"
        return "disabled"

    @property
    def google_redirect_uri(self) -> str:
        return f"{self.app_base_url.rstrip('/')}/api/index.php/auth/google/callback"

    @property
    def google_bridge_complete_uri(self) -> str:
        return f"{self.app_base_url.rstrip('/')}/api/index.php/auth/google/bridge-complete"


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
    merged = {
        "Content-Type": "application/json; charset=utf-8",
        "Cache-Control": "no-store",
        **SECURITY_HEADERS,
    }
    if headers:
        merged.update(headers)
    return PortalResponse(int(status), merged, json.dumps(payload, ensure_ascii=False))


def _load_json(body: bytes) -> dict[str, Any]:
    if not body:
        return {}
    try:
        payload = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_payload(headers: dict[str, str], body: bytes) -> dict[str, Any]:
    content_type = headers.get("content-type", "").lower()
    if "application/x-www-form-urlencoded" in content_type:
        try:
            parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True)
        except UnicodeDecodeError:
            return {}
        normalized: dict[str, Any] = {}
        for key, values in parsed.items():
            normalized[str(key)] = values[0] if len(values) == 1 else values
        return normalized
    return _load_json(body)
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


def _provider_catalog() -> list[dict[str, Any]]:
    return [{"name": name, "fields": PROVIDER_FIELDS.get(name, ["api_key"])} for name in SUPPORTED_PROVIDERS]


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


def _verification_payload(config: PortalConfig, store: PortalStateStore, user_id: str) -> dict[str, Any]:
    return store.request_email_verification(
        user_id=user_id,
        app_base_url=config.app_base_url,
        debug_token=config.debug_auth_tokens,
    )


def _google_oauth_payload(config: PortalConfig) -> dict[str, Any]:
    return {
        "enabled": config.google_oauth_enabled,
        "provider": "google",
        "mode": config.google_oauth_mode,
        "client_id": config.google_client_id if config.google_oauth_enabled else "",
        "bridge_url": config.google_bridge_url if config.google_oauth_mode == "bridge_id_token" else "",
        "redirect_uri": config.google_redirect_uri,
    }


def _redirect_response(location: str, *, status: HTTPStatus = HTTPStatus.FOUND, headers: dict[str, str] | None = None) -> PortalResponse:
    merged = {"Location": location, "Cache-Control": "no-store", **SECURITY_HEADERS}
    if headers:
        merged.update(headers)
    return PortalResponse(int(status), merged, "")


def _append_query(url: str, **params: str) -> str:
    parts = urlsplit(url)
    existing = parse_qs(parts.query, keep_blank_values=True)
    for key, value in params.items():
        existing[str(key)] = [str(value)]
    query = urlencode(existing, doseq=True)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def _google_authorize_location(config: PortalConfig, state_token: str) -> str:
    query = urlencode(
        {
            "client_id": config.google_client_id,
            "redirect_uri": config.google_redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state_token,
            "access_type": "online",
            "prompt": "select_account",
        }
    )
    return f"{config.google_authorize_url}?{query}"


def _google_bridge_location(config: PortalConfig, state_token: str) -> str:
    return _append_query(
        config.google_bridge_url,
        state=state_token,
        return_to=config.google_bridge_complete_uri,
        client_id=config.google_client_id,
    )


def _exchange_google_code(config: PortalConfig, code: str) -> dict[str, Any]:
    body = urlencode(
        {
            "code": code,
            "client_id": config.google_client_id,
            "client_secret": config.google_client_secret,
            "redirect_uri": config.google_redirect_uri,
            "grant_type": "authorization_code",
        }
    ).encode("utf-8")
    req = request.Request(
        config.google_token_url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
    )
    try:
        with request.urlopen(req, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise ValueError(f"Google token exchange failed: {details or exc.reason}") from exc
    except error.URLError as exc:
        raise ValueError(f"Google token exchange failed: {exc.reason}") from exc


def _fetch_google_profile(config: PortalConfig, access_token: str) -> dict[str, Any]:
    req = request.Request(
        config.google_userinfo_url,
        method="GET",
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
    )
    try:
        with request.urlopen(req, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise ValueError(f"Google userinfo request failed: {details or exc.reason}") from exc
    except error.URLError as exc:
        raise ValueError(f"Google userinfo request failed: {exc.reason}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Google userinfo payload was invalid.")
    email = str(payload.get("email", "")).strip().lower()
    if not email:
        raise ValueError("Google account did not return an email.")
    payload["email"] = email
    return payload


def _verify_google_id_token(config: PortalConfig, credential: str) -> dict[str, Any]:
    token = credential.strip()
    if not token:
        raise ValueError("missing_google_credential")
    req = request.Request(
        f"{config.google_tokeninfo_url}?{urlencode({'id_token': token})}",
        method="GET",
        headers={"Accept": "application/json"},
    )
    try:
        with request.urlopen(req, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise ValueError(f"Google token verification failed: {details or exc.reason}") from exc
    except error.URLError as exc:
        raise ValueError(f"Google token verification failed: {exc.reason}") from exc
    if not isinstance(payload, dict):
        raise ValueError("google_token_payload_invalid")
    issuer = str(payload.get("iss", "")).strip()
    if issuer not in {"https://accounts.google.com", "accounts.google.com"}:
        raise ValueError("google_token_issuer_invalid")
    audience = str(payload.get("aud", "")).strip()
    authorized_party = str(payload.get("azp", "")).strip()
    if audience != config.google_client_id and authorized_party != config.google_client_id:
        raise ValueError("google_token_audience_mismatch")
    expires_at = int(str(payload.get("exp", "0") or "0"))
    if expires_at and expires_at <= int(time.time()):
        raise ValueError("google_token_expired")
    email = str(payload.get("email", "")).strip().lower()
    if not email:
        raise ValueError("google_account_missing_email")
    payload["email"] = email
    email_verified = str(payload.get("email_verified", "")).strip().lower()
    payload["email_verified"] = email_verified in {"true", "1", "yes"}
    return payload


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
    payload = _load_payload(headers, body)
    query = parse_qs(query_string, keep_blank_values=True)

    try:
        if method == "GET" and route == "/health":
            return json_response(
                {
                    "ok": True,
                    "manager_email": config.manager_email,
                    "auth_features": ["email_verification", "password_reset"],
                    "google_oauth": _google_oauth_payload(config),
                    "app_base_url": config.app_base_url,
                }
            )

        if method == "GET" and route == "/auth/me":
            user = _get_current_user(store, headers)
            if user is None:
                return json_response(
                    {
                        "authenticated": False,
                        "manager_email": config.manager_email,
                        "google_oauth": _google_oauth_payload(config),
                        "app_base_url": config.app_base_url,
                    }
                )
            return json_response(
                {
                    "authenticated": True,
                    "user": user,
                    "manager_email": config.manager_email,
                    "google_oauth": _google_oauth_payload(config),
                    "app_base_url": config.app_base_url,
                }
            )

        if method == "GET" and route == "/auth/device/status":
            device_code = str(query.get("device_code", [""])[0]).strip()
            if not device_code:
                return json_response({"error": "device_code is required."}, status=HTTPStatus.BAD_REQUEST)
            status_payload = store.get_device_login_status(token=device_code, ttl_days=config.auth_session_days)
            headers_out = None
            if status_payload.get("status") == "approved" and status_payload.get("session_token"):
                headers_out = {"Set-Cookie": _cookie_header(str(status_payload["session_token"]), path=config.cookie_path)}
                status_payload = {
                    "authenticated": True,
                    "user": status_payload.get("user"),
                    "google_oauth": _google_oauth_payload(config),
                    "status": "approved",
                }
            return json_response(status_payload, headers=headers_out)

        if method == "GET" and route == "/auth/google/start":
            if config.google_oauth_mode not in {"oauth_code", "bridge_id_token"}:
                return json_response({"error": "Google OAuth is not configured."}, status=HTTPStatus.SERVICE_UNAVAILABLE)
            redirect_path = str(query.get("redirect_path", ["/"])[0]).strip() or "/"
            if not redirect_path.startswith("/"):
                redirect_path = "/"
            state_record = store.create_auth_token(
                user_id="google-oauth",
                kind="google_oauth_state",
                ttl_hours=1,
                metadata={"redirect_path": redirect_path},
            )
            if config.google_oauth_mode == "bridge_id_token":
                return _redirect_response(_google_bridge_location(config, state_record["raw_token"]))
            return _redirect_response(_google_authorize_location(config, state_record["raw_token"]))

        if method == "GET" and route == "/auth/google/callback":
            if config.google_oauth_mode != "oauth_code":
                return _redirect_response(_append_query(f"{config.app_base_url.rstrip('/')}/", oauth_error="google_not_configured"))
            oauth_error = str(query.get("error", [""])[0]).strip()
            if oauth_error:
                return _redirect_response(_append_query(f"{config.app_base_url.rstrip('/')}/", oauth_error=oauth_error))
            state_token = str(query.get("state", [""])[0]).strip()
            code = str(query.get("code", [""])[0]).strip()
            state = store.consume_auth_token(token=state_token, kind="google_oauth_state")
            if not state:
                return _redirect_response(_append_query(f"{config.app_base_url.rstrip('/')}/", oauth_error="invalid_state"))
            if not code:
                return _redirect_response(_append_query(f"{config.app_base_url.rstrip('/')}/", oauth_error="missing_code"))
            try:
                tokens = _exchange_google_code(config, code)
                access_token = str(tokens.get("access_token", "")).strip()
                if not access_token:
                    raise ValueError("missing_access_token")
                profile = _fetch_google_profile(config, access_token)
                user = store.upsert_google_user(
                    email=str(profile.get("email", "")).strip(),
                    display_name=str(profile.get("name", "")).strip(),
                    manager_email=config.manager_email,
                    email_verified=bool(profile.get("email_verified", True)),
                )
            except ValueError as exc:
                return _redirect_response(
                    _append_query(f"{config.app_base_url.rstrip('/')}/", oauth_error=str(exc).replace(" ", "_"))
                )
            session = store.create_session(user_id=user.user_id, ttl_days=config.auth_session_days)
            redirect_path = str(state.get("metadata", {}).get("redirect_path", "/")).strip() or "/"
            location = _append_query(f"{config.app_base_url.rstrip('/')}{redirect_path}", oauth="google_success")
            return _redirect_response(
                location,
                headers={"Set-Cookie": _cookie_header(session["token"], path=config.cookie_path)},
            )

        if method == "POST" and route == "/auth/google/id-token":
            if config.google_oauth_mode != "id_token":
                return json_response({"error": "Google ID token sign-in is not configured."}, status=HTTPStatus.SERVICE_UNAVAILABLE)
            credential = str(payload.get("credential", "")).strip()
            try:
                profile = _verify_google_id_token(config, credential)
                user = store.upsert_google_user(
                    email=str(profile.get("email", "")).strip(),
                    display_name=str(profile.get("name", "")).strip(),
                    manager_email=config.manager_email,
                    email_verified=bool(profile.get("email_verified", True)),
                )
            except ValueError as exc:
                return json_response({"error": str(exc)}, status=HTTPStatus.UNAUTHORIZED)
            session = store.create_session(user_id=user.user_id, ttl_days=config.auth_session_days)
            return json_response(
                {
                    "authenticated": True,
                    "user": user.to_dict(),
                    "google_oauth": _google_oauth_payload(config),
                    "message": "Google sign-in completed.",
                },
                headers={"Set-Cookie": _cookie_header(session["token"], path=config.cookie_path)},
            )

        if method == "POST" and route == "/auth/device/start":
            payload_display_name = str(payload.get("display_name", "")).strip()
            payload_mode = str(payload.get("mode", "browser")).strip().lower() or "browser"
            issued = store.create_device_login(
                app_base_url=config.app_base_url,
                display_name=payload_display_name,
                mode=payload_mode,
            )
            return json_response(issued)

        if method == "POST" and route == "/auth/device/complete":
            user = _require_user(store, headers)
            device_code = str(payload.get("device_code", "")).strip()
            if not device_code:
                return json_response({"error": "device_code is required."}, status=HTTPStatus.BAD_REQUEST)
            try:
                completed = store.complete_device_login(token=device_code, user_id=str(user["user_id"]))
            except ValueError as exc:
                return json_response({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return json_response(
                {
                    "status": completed.get("status", "approved"),
                    "message": "Desktop sign-in completed. You can return to FORGE Desktop.",
                }
            )

        if method == "POST" and route == "/auth/google/bridge-complete":
            if config.google_oauth_mode != "bridge_id_token":
                return _redirect_response(_append_query(f"{config.app_base_url.rstrip('/')}/", oauth_error="google_bridge_not_configured"))
            credential = str(payload.get("credential", "")).strip()
            state_token = str(payload.get("state", "")).strip()
            state = store.consume_auth_token(token=state_token, kind="google_oauth_state")
            if not state:
                return _redirect_response(_append_query(f"{config.app_base_url.rstrip('/')}/", oauth_error="invalid_state"))
            try:
                profile = _verify_google_id_token(config, credential)
                user = store.upsert_google_user(
                    email=str(profile.get("email", "")).strip(),
                    display_name=str(profile.get("name", "")).strip(),
                    manager_email=config.manager_email,
                    email_verified=bool(profile.get("email_verified", True)),
                )
            except ValueError as exc:
                return _redirect_response(
                    _append_query(f"{config.app_base_url.rstrip('/')}/", oauth_error=str(exc).replace(" ", "_"))
                )
            session = store.create_session(user_id=user.user_id, ttl_days=config.auth_session_days)
            redirect_path = str(state.get("metadata", {}).get("redirect_path", "/")).strip() or "/"
            location = _append_query(f"{config.app_base_url.rstrip('/')}{redirect_path}", oauth="google_success")
            return _redirect_response(
                location,
                headers={"Set-Cookie": _cookie_header(session["token"], path=config.cookie_path)},
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
                {
                    "authenticated": True,
                    "user": user.to_dict(),
                    "google_oauth": _google_oauth_payload(config),
                    "verification": _verification_payload(config, store, user.user_id),
                },
                headers={"Set-Cookie": _cookie_header(session["token"], path=config.cookie_path)},
            )

        if method == "POST" and route == "/auth/login":
            user = store.authenticate_user(
                email=str(payload.get("email", "")).strip(),
                password=str(payload.get("password", "")),
            )
            if user is None:
                return json_response({"error": "Invalid email or password."}, status=HTTPStatus.UNAUTHORIZED)
            session = store.create_session(user_id=user.user_id, ttl_days=config.auth_session_days)
            return json_response(
                {"authenticated": True, "user": user.to_dict(), "google_oauth": _google_oauth_payload(config)},
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

        if method == "POST" and route == "/auth/request-verification":
            user = _require_user(store, headers)
            return json_response({"verification": _verification_payload(config, store, str(user["user_id"]))})

        if method == "POST" and route == "/auth/verify-email":
            token = str(payload.get("token", "")).strip() or str(query.get("token", [""])[0]).strip()
            user = store.verify_email(token=token)
            session_token = _read_session_token(headers)
            current = store.get_session(session_token) if session_token else None
            return json_response(
                {
                    "authenticated": bool(current and current.get("user_id") == user.user_id),
                    "user": user.to_dict() if current and current.get("user_id") == user.user_id else None,
                    "message": "Email verified successfully.",
                }
            )

        if method == "POST" and route == "/auth/request-password-reset":
            email = str(payload.get("email", "")).strip()
            return json_response(
                {
                    "reset": store.request_password_reset(
                        email=email,
                        app_base_url=config.app_base_url,
                        debug_token=config.debug_auth_tokens,
                    )
                }
            )

        if method == "POST" and route == "/auth/reset-password":
            token = str(payload.get("token", "")).strip()
            password = str(payload.get("password", ""))
            user = store.reset_password(token=token, new_password=password)
            session = store.create_session(user_id=user.user_id, ttl_days=config.auth_session_days)
            return json_response(
                {
                    "authenticated": True,
                    "user": user.to_dict(),
                    "google_oauth": _google_oauth_payload(config),
                    "message": "Password reset complete.",
                },
                headers={"Set-Cookie": _cookie_header(session["token"], path=config.cookie_path)},
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

        if method == "GET" and route == "/user/keys/export":
            user = _require_user(store, headers)
            return json_response({"secrets": store.export_user_provider_secrets(str(user["user_id"]))})

        if method == "POST" and route == "/user/keys":
            user = _require_user(store, headers)
            provider = str(payload.get("provider", "")).strip().lower()
            if provider not in SUPPORTED_PROVIDERS:
                return json_response({"error": "Unsupported provider."}, status=HTTPStatus.BAD_REQUEST)
            secret_payload: dict[str, str] = {}
            for key, value in payload.items():
                if key not in ALLOWED_SECRET_FIELDS:
                    continue
                normalized = normalize_secret_value(key, str(value))
                if normalized:
                    secret_payload[key] = normalized
            if secret_payload:
                store.save_user_provider_secret(user_id=str(user["user_id"]), provider=provider, payload=secret_payload)
            else:
                store.delete_user_provider_secret(user_id=str(user["user_id"]), provider=provider)
            return json_response(
                {
                    "providers": _provider_catalog(),
                    "saved": store.list_user_provider_secrets(str(user["user_id"])),
                    "viewer": user,
                }
            )

        if method == "POST" and route == "/desktop/missions/sync":
            user = _require_user(store, headers)
            store.upsert_mission_event(
                user_id=str(user["user_id"]),
                mission_id=str(payload.get("mission_id", "")).strip(),
                objective=str(payload.get("objective", "")).strip() or "Untitled mission",
                status=str(payload.get("status", "")).strip() or "unknown",
                validation_status=str(payload.get("validation_status", "")).strip() or "unknown",
                summary=str(payload.get("summary", "")).strip() or "No mission summary.",
                workspace_root=str(payload.get("workspace_root", "")).strip(),
                source=str(payload.get("source", "desktop")).strip() or "desktop",
            )
            return json_response({"ok": True})

        if method == "POST" and route == "/desktop/approvals/sync":
            user = _require_user(store, headers)
            approvals = payload.get("approvals", [])
            if isinstance(approvals, list):
                for item in approvals:
                    if not isinstance(item, dict):
                        continue
                    approval_id = str(item.get("approval_id", "")).strip()
                    if not approval_id:
                        continue
                    store.upsert_approval_event(
                        user_id=str(user["user_id"]),
                        approval_id=approval_id,
                        mission_id=str(item.get("mission_id", "")).strip(),
                        step_id=str(item.get("step_id", "")).strip(),
                        approval_class=str(item.get("approval_class", "")).strip() or "unknown",
                        status=str(item.get("status", "")).strip() or "pending",
                        summary=str(item.get("summary", "")).strip() or "Approval request",
                        request_excerpt=str(item.get("request_excerpt", "")).strip(),
                        source=str(item.get("source", "desktop")).strip() or "desktop",
                    )
            return json_response({"ok": True, "count": len(approvals) if isinstance(approvals, list) else 0})

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

        if method == "GET" and route == "/admin/approvals":
            _require_admin(store, headers)
            return json_response({"approvals": store.list_approval_events(limit=100)})

        if method == "GET" and route == "/admin/missions":
            _require_admin(store, headers)
            return json_response({"missions": store.list_missions(limit=100)})

        if method == "GET" and route == "/admin/key-health":
            _require_admin(store, headers)
            return json_response({"key_health": store.list_user_key_health()})

        if method == "GET" and route == "/admin/outbox":
            _require_admin(store, headers)
            return json_response({"outbox": store.list_outbox_messages(limit=100)})

        return json_response({"error": "Not found."}, status=HTTPStatus.NOT_FOUND)
    except PermissionError as exc:
        message = str(exc)
        status = HTTPStatus.FORBIDDEN if "Admin" in message else HTTPStatus.UNAUTHORIZED
        return json_response({"error": message}, status=status)
    except ValueError as exc:
        return json_response({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
