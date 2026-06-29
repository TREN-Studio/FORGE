"""
FORGE × GitHub Tool Integration
===============================
Wraps GitHub Contents API actions inside the ForgeTool interface.
"""

from __future__ import annotations

import base64
import urllib.request
import urllib.error
import json
from typing import Any

from forge.tools.base import ForgeTool, ToolResult


class GitHubTool(ForgeTool):
    name = "github"
    description = "Publish files and manage GitHub repository content"
    risk_class = "publish"
    requires_auth = ["github_token"]
    available_actions = ["publish", "get_file"]

    def __init__(self, token_resolver: Any | None = None) -> None:
        self._token_resolver = token_resolver

    async def execute(self, action: str, params: dict[str, Any]) -> ToolResult:
        token = params.get("token")
        if not token and self._token_resolver:
            token = self._token_resolver("github_token")

        if not token:
            if params.get("mock") or params.get("demo"):
                return ToolResult(
                    success=True,
                    data="https://github.com/mock-owner/mock-repo/blob/main/mock-file.txt",
                    action_taken="Mocked file publishing to GitHub.",
                )
            return ToolResult(
                success=False,
                error="GitHub token is not configured. Connect using: forge tools connect github",
            )

        try:
            if action == "publish":
                owner = params.get("owner")
                repo = params.get("repo")
                path = params.get("path")
                content = params.get("content", "")
                branch = params.get("branch", "main")
                commit_message = params.get("commit_message", "Published via FORGE")

                if not owner or not repo or not path:
                    return ToolResult(success=False, error="owner, repo, and path parameters are required")

                api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
                
                # Check if file exists to get its SHA
                sha = None
                req = urllib.request.Request(api_url)
                req.add_header("Authorization", f"token {token}")
                req.add_header("Accept", "application/vnd.github.v3+json")
                req.add_header("User-Agent", "FORGE-Agent")
                
                try:
                    with urllib.request.urlopen(req) as resp:
                        data = json.loads(resp.read().decode())
                        sha = data.get("sha")
                except urllib.error.HTTPError as err:
                    if err.code != 404:
                        return ToolResult(success=False, error=f"GitHub check failed: {err.read().decode()}")

                # Upload content
                body = {
                    "message": commit_message,
                    "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
                    "branch": branch,
                }
                if sha:
                    body["sha"] = sha

                put_req = urllib.request.Request(api_url, data=json.dumps(body).encode(), method="PUT")
                put_req.add_header("Authorization", f"token {token}")
                put_req.add_header("Content-Type", "application/json")
                put_req.add_header("User-Agent", "FORGE-Agent")

                with urllib.request.urlopen(put_req) as resp:
                    resp_data = json.loads(resp.read().decode())
                    html_url = resp_data.get("content", {}).get("html_url", api_url)
                    return ToolResult(success=True, data=html_url, action_taken=f"Published {path} on GitHub.")
            
            elif action == "get_file":
                owner = params.get("owner")
                repo = params.get("repo")
                path = params.get("path")
                branch = params.get("branch", "main")

                if not owner or not repo or not path:
                    return ToolResult(success=False, error="owner, repo, and path parameters are required")

                api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={branch}"
                req = urllib.request.Request(api_url)
                req.add_header("Authorization", f"token {token}")
                req.add_header("Accept", "application/vnd.github.v3+json")
                req.add_header("User-Agent", "FORGE-Agent")

                with urllib.request.urlopen(req) as resp:
                    data = json.loads(resp.read().decode())
                    raw_content = base64.b64decode(data.get("content", "")).decode("utf-8")
                    return ToolResult(success=True, data=raw_content)
            else:
                return ToolResult(success=False, error=f"Unsupported action: {action}")
        except Exception as exc:
            return ToolResult(success=False, error=str(exc))
