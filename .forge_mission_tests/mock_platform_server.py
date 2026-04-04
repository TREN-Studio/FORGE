from __future__ import annotations

import base64
import json
from hashlib import sha1
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse


ROOT = Path(__file__).resolve().parent
GITHUB_LOG = ROOT / "mock_github_request.json"
GITHUB_STATE = ROOT / "mock_github_state.json"
WORDPRESS_LOG = ROOT / "mock_wordpress_request.json"


def _load_state() -> dict:
    if GITHUB_STATE.exists():
        return json.loads(GITHUB_STATE.read_text(encoding="utf-8"))
    return {"files": {}}


def _save_state(state: dict) -> None:
    GITHUB_STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "FORGE-MockPlatform/1.0"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path.startswith("/github/repos/") and "/contents/" in parsed.path:
            state = _load_state()
            path_without_prefix = parsed.path.removeprefix("/github/repos/")
            repo_part, content_path = path_without_prefix.split("/contents/", 1)
            key = f"{repo_part}/{unquote(content_path)}"
            file_entry = state["files"].get(key)
            if not file_entry:
                self.send_error(404, "Not found")
                return
            self._send_json(
                {
                    "sha": file_entry["sha"],
                    "path": unquote(content_path),
                }
            )
            return
        self.send_error(404, "Not found")

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path.startswith("/wordpress/wp-json/wp/v2/"):
            body = self._read_json()
            auth = self.headers.get("Authorization", "")
            expected = base64.b64encode(b"forge-user:forge-app-password").decode("ascii")
            payload = {
                "path": parsed.path,
                "headers": dict(self.headers.items()),
                "body": body,
                "authorized": auth == f"Basic {expected}",
            }
            WORDPRESS_LOG.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            resource_type = parsed.path.rstrip("/").split("/")[-1]
            self._send_json(
                {
                    "id": 4242,
                    "status": body.get("status", "publish"),
                    "link": f"http://127.0.0.1:18912/wordpress/{resource_type}/4242",
                }
            )
            return
        self.send_error(404, "Not found")

    def do_PUT(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path.startswith("/github/repos/") and "/contents/" in parsed.path:
            body = self._read_json()
            path_without_prefix = parsed.path.removeprefix("/github/repos/")
            repo_part, content_path = path_without_prefix.split("/contents/", 1)
            decoded_path = unquote(content_path)
            key = f"{repo_part}/{decoded_path}"
            raw_content = base64.b64decode(body.get("content", "").encode("ascii")).decode("utf-8", errors="replace")
            sha = sha1(raw_content.encode("utf-8")).hexdigest()
            state = _load_state()
            state["files"][key] = {
                "sha": sha,
                "content": raw_content,
                "message": body.get("message", ""),
                "branch": body.get("branch", "main"),
            }
            _save_state(state)
            GITHUB_LOG.write_text(
                json.dumps(
                    {
                        "path": parsed.path,
                        "headers": dict(self.headers.items()),
                        "body": body,
                        "decoded_content": raw_content,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            owner, repo = repo_part.split("/", 1)
            self._send_json(
                {
                    "content": {
                        "path": decoded_path,
                        "html_url": f"https://github.com/{owner}/{repo}/blob/{body.get('branch', 'main')}/{decoded_path}",
                    },
                    "commit": {
                        "sha": sha,
                    },
                }
            )
            return
        self.send_error(404, "Not found")

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8"))

    def _send_json(self, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 18912), Handler)
    try:
        server.serve_forever()
    finally:
        server.server_close()
