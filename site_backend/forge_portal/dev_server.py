from __future__ import annotations

import argparse
import json
import shutil
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

SCRIPT_ROOT = Path(__file__).resolve().parent
REPO_ROOT = Path(__file__).resolve().parents[2] if len(Path(__file__).resolve().parents) >= 3 else SCRIPT_ROOT
for candidate in (SCRIPT_ROOT, REPO_ROOT):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

try:
    from site_backend.forge_portal.api import PortalConfig, handle_request
    from site_backend.forge_portal.store import PortalStateStore
except ModuleNotFoundError:
    from api import PortalConfig, handle_request
    from store import PortalStateStore


class PortalDevHandler(BaseHTTPRequestHandler):
    server: "PortalDevServer"

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        if parsed.path in {"/", "/index.html"}:
            self._send_html(self.server.portal_html)
            return
        if parsed.path.startswith("/mock/google/"):
            self._dispatch_mock_google("GET", parsed)
            return
        if parsed.path.startswith("/api/") or parsed.path.startswith("/api/index.php"):
            self._dispatch_api("GET")
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlsplit(self.path)
        if parsed.path.startswith("/mock/google/"):
            self._dispatch_mock_google("POST", parsed)
            return
        if parsed.path.startswith("/api/") or parsed.path.startswith("/api/index.php"):
            self._dispatch_api("POST")
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return

    def _dispatch_api(self, method: str) -> None:
        parsed = urlsplit(self.path)
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b""
        api_path = parsed.path
        if api_path.startswith("/api/index.php"):
            api_path = api_path[len("/api/index.php") :] or "/"
        response = handle_request(
            self.server.portal_config,
            self.server.store,
            method=method,
            path=api_path,
            query_string=parsed.query,
            headers={key: value for key, value in self.headers.items()},
            body=body,
        )
        status, headers, payload = response.to_http()
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(payload)

    def _dispatch_mock_google(self, method: str, parsed) -> None:
        if parsed.path == "/mock/google/authorize" and method == "GET":
            query = parse_qs(parsed.query, keep_blank_values=True)
            redirect_uri = str(query.get("redirect_uri", [""])[0]).strip()
            state = str(query.get("state", [""])[0]).strip()
            if not redirect_uri or not state:
                self.send_error(HTTPStatus.BAD_REQUEST)
                return
            location = f"{redirect_uri}?{urlencode({'code': 'mock-google-code', 'state': state})}"
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", location)
            self.end_headers()
            return

        if parsed.path == "/mock/google/token" and method == "POST":
            payload = {
                "access_token": "mock-google-access-token",
                "token_type": "Bearer",
                "expires_in": 3600,
            }
            raw = json.dumps(payload).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return

        if parsed.path == "/mock/google/userinfo" and method == "GET":
            payload = self.server.mock_google_profile
            raw = json.dumps(payload).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return

        self.send_error(HTTPStatus.NOT_FOUND)

    def _send_html(self, html: str) -> None:
        payload = html.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class PortalDevServer(ThreadingHTTPServer):
    def __init__(self, host: str, port: int, *, portal_html: str, portal_config: PortalConfig) -> None:
        super().__init__((host, port), PortalDevHandler)
        self.portal_html = portal_html
        self.portal_config = portal_config
        self.store = PortalStateStore(portal_config.state_root)
        self.mock_google_profile = {
            "sub": "mock-google-user",
            "email": "google-user@example.com",
            "email_verified": True,
            "name": "Google User",
        }

    def server_close(self) -> None:
        try:
            self.store.close()
        finally:
            super().server_close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve the FORGE public portal locally.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=43017)
    parser.add_argument("--state-root", default=".forge_artifacts/portal-dev-state")
    parser.add_argument("--site-root", default="site/portal")
    parser.add_argument("--reset-state", action="store_true")
    args = parser.parse_args()

    site_root = Path(args.site_root).resolve()
    state_root = Path(args.state_root).resolve()
    if args.reset_state and state_root.exists():
        shutil.rmtree(state_root)
    state_root.mkdir(parents=True, exist_ok=True)
    portal_html = (site_root / "index.html").read_text(encoding="utf-8")
    config = PortalConfig(
        state_root=state_root,
        manager_email="larbilife@gmail.com",
        cookie_path="/",
        auth_session_days=30,
        app_base_url=f"http://{args.host}:{args.port}",
        debug_auth_tokens=True,
        google_client_id="forge-local-google-client",
        google_client_secret="forge-local-google-secret",
        google_authorize_url=f"http://{args.host}:{args.port}/mock/google/authorize",
        google_token_url=f"http://{args.host}:{args.port}/mock/google/token",
        google_userinfo_url=f"http://{args.host}:{args.port}/mock/google/userinfo",
    )
    server = PortalDevServer(args.host, args.port, portal_html=portal_html, portal_config=config)
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
