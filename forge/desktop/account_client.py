from __future__ import annotations

import json
from dataclasses import dataclass
from http.cookies import SimpleCookie
from typing import Any
from urllib import error, request


SESSION_COOKIE_NAME = "forge_portal_session"


class PortalApiError(RuntimeError):
    def __init__(self, message: str, *, status: int = 500, payload: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.payload = payload or {"error": message}


@dataclass
class PortalApiReply:
    status: int
    payload: dict[str, Any]
    session_token: str | None = None


class PortalAccountClient:
    def __init__(self, base_url: str, *, timeout_seconds: int = 20) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def auth_me(self, session_token: str) -> dict[str, Any]:
        return self._request("GET", "/auth/me", session_token=session_token).payload

    def register(self, payload: dict[str, Any]) -> PortalApiReply:
        return self._request("POST", "/auth/register", payload=payload)

    def login(self, payload: dict[str, Any]) -> PortalApiReply:
        return self._request("POST", "/auth/login", payload=payload)

    def logout(self, session_token: str) -> PortalApiReply:
        return self._request("POST", "/auth/logout", session_token=session_token)

    def request_verification(self, session_token: str) -> dict[str, Any]:
        return self._request("POST", "/auth/request-verification", session_token=session_token).payload

    def verify_email(self, token: str, session_token: str | None = None) -> dict[str, Any]:
        return self._request("POST", "/auth/verify-email", payload={"token": token}, session_token=session_token).payload

    def request_password_reset(self, email: str) -> dict[str, Any]:
        return self._request("POST", "/auth/request-password-reset", payload={"email": email}).payload

    def reset_password(self, token: str, password: str) -> PortalApiReply:
        return self._request("POST", "/auth/reset-password", payload={"token": token, "password": password})

    def start_device_login(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._request("POST", "/auth/device/start", payload=payload or {}).payload

    def device_login_status(self, device_code: str) -> PortalApiReply:
        return self._request("GET", f"/auth/device/status?device_code={device_code}")

    def list_user_keys(self, session_token: str) -> dict[str, Any]:
        return self._request("GET", "/user/keys", session_token=session_token).payload

    def save_user_key(self, session_token: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/user/keys", payload=payload, session_token=session_token).payload

    def export_user_secrets(self, session_token: str) -> dict[str, dict[str, str]]:
        payload = self._request("GET", "/user/keys/export", session_token=session_token).payload
        return payload.get("secrets", {})

    def admin_overview(self, session_token: str) -> dict[str, Any]:
        return self._request("GET", "/admin/overview", session_token=session_token).payload

    def admin_users(self, session_token: str) -> dict[str, Any]:
        return self._request("GET", "/admin/users", session_token=session_token).payload

    def admin_approvals(self, session_token: str) -> dict[str, Any]:
        return self._request("GET", "/admin/approvals", session_token=session_token).payload

    def admin_missions(self, session_token: str) -> dict[str, Any]:
        return self._request("GET", "/admin/missions", session_token=session_token).payload

    def admin_key_health(self, session_token: str) -> dict[str, Any]:
        return self._request("GET", "/admin/key-health", session_token=session_token).payload

    def admin_outbox(self, session_token: str) -> dict[str, Any]:
        return self._request("GET", "/admin/outbox", session_token=session_token).payload

    def sync_mission(self, session_token: str, payload: dict[str, Any]) -> None:
        self._request("POST", "/desktop/missions/sync", payload=payload, session_token=session_token)

    def sync_approvals(self, session_token: str, payload: dict[str, Any]) -> None:
        self._request("POST", "/desktop/approvals/sync", payload=payload, session_token=session_token)

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        session_token: str | None = None,
    ) -> PortalApiReply:
        url = f"{self.base_url}{path}"
        body = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if session_token:
            headers["Cookie"] = f"{SESSION_COOKIE_NAME}={session_token}"
        req = request.Request(url, data=body, method=method, headers=headers)
        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as response:
                raw_body = response.read().decode("utf-8")
                reply_headers = response.headers
                status = int(getattr(response, "status", 200))
        except error.HTTPError as exc:
            raw_body = exc.read().decode("utf-8", errors="replace")
            payload_data = self._decode_payload(raw_body)
            raise PortalApiError(payload_data.get("error", str(exc)), status=int(exc.code), payload=payload_data) from exc
        except error.URLError as exc:
            raise PortalApiError(f"Portal connection failed: {exc.reason}", status=502) from exc

        payload_data = self._decode_payload(raw_body)
        session = self._extract_cookie(reply_headers.get_all("Set-Cookie", []))
        return PortalApiReply(status=status, payload=payload_data, session_token=session)

    @staticmethod
    def _decode_payload(raw_body: str) -> dict[str, Any]:
        if not raw_body:
            return {}
        try:
            parsed = json.loads(raw_body)
        except json.JSONDecodeError:
            return {"error": "Portal returned invalid JSON."}
        if isinstance(parsed, dict):
            return parsed
        return {"data": parsed}

    @staticmethod
    def _extract_cookie(values: list[str]) -> str | None:
        cookie = SimpleCookie()
        for value in values:
            cookie.load(value)
        morsel = cookie.get(SESSION_COOKIE_NAME)
        if morsel is None:
            return None
        return morsel.value.strip() or None
