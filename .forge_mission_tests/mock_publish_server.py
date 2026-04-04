from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parent
REQUEST_LOG = ROOT / "mock_publish_request.json"


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8", errors="replace")
        payload = {
            "path": self.path,
            "headers": {key: value for key, value in self.headers.items()},
            "body": body,
            "bytes": len(body.encode("utf-8")),
        }
        REQUEST_LOG.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        response = json.dumps(
            {
                "ok": True,
                "received_bytes": payload["bytes"],
                "received_path": self.path,
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 18911), Handler)
    try:
        server.serve_forever()
    finally:
        server.server_close()
