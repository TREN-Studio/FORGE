from __future__ import annotations

import json
from urllib import request

from forge.config.settings import OperatorSettings


class ExternalPublisher:
    """Publish structured content to an external HTTP endpoint."""

    def __init__(self, settings: OperatorSettings) -> None:
        self._settings = settings

    def publish(
        self,
        *,
        target_url: str,
        content: str,
        objective: str,
        method: str = "POST",
        timeout_seconds: int | None = None,
    ) -> dict:
        body = {
            "objective": objective,
            "content": content,
        }
        encoded = json.dumps(body).encode("utf-8")
        req = request.Request(
            target_url,
            data=encoded,
            method=method.upper(),
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": "FORGE/1.1 external-publisher",
            },
        )
        timeout = timeout_seconds or max(5, self._settings.shell_timeout_seconds)
        with request.urlopen(req, timeout=timeout) as response:
            response_body = response.read().decode("utf-8", errors="replace")
            return {
                "status": "completed",
                "target_url": target_url,
                "http_method": method.upper(),
                "response_status": int(response.status),
                "response_body_preview": response_body[:1200],
                "published_bytes": len(encoded),
                "summary": f"Published {len(encoded)} byte(s) to {target_url} with HTTP {response.status}.",
                "evidence": [f"url:{target_url}", f"http_status:{response.status}", f"bytes:{len(encoded)}"],
            }
