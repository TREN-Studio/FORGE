from __future__ import annotations

import argparse
import shutil
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

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
        if self.path in {"/", "/index.html"}:
            self._send_html(self.server.portal_html)
            return
        if self.path.startswith("/api/") or self.path.startswith("/api/index.php"):
            self._dispatch_api("GET")
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if self.path.startswith("/api/") or self.path.startswith("/api/index.php"):
            self._dispatch_api("POST")
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return

    def _dispatch_api(self, method: str) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b""
        api_path = self.path
        if api_path.startswith("/api/index.php"):
            api_path = api_path[len("/api/index.php") :] or "/"
        response = handle_request(
            self.server.portal_config,
            self.server.store,
            method=method,
            path=api_path,
            query_string="",
            headers={key: value for key, value in self.headers.items()},
            body=body,
        )
        status, headers, payload = response.to_http()
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(payload)

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
    )
    server = PortalDevServer(args.host, args.port, portal_html=portal_html, portal_config=config)
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
