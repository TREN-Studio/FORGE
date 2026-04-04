from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from urllib import error, parse, request

from forge.config.settings import OperatorSettings
from forge.tools.credentials import CredentialResolver


@dataclass(slots=True)
class GitHubTarget:
    owner: str
    repo: str
    path: str
    branch: str
    api_base: str


class GitHubPublisher:
    """Publish mission outputs to a GitHub repository through the Contents API."""

    def __init__(self, settings: OperatorSettings) -> None:
        self._settings = settings
        self._resolver = CredentialResolver(settings)

    def publish(
        self,
        *,
        objective: str,
        content: str,
        target_repo: str = "",
        repo_path: str = "",
        branch: str = "",
        commit_message: str = "",
        api_base: str = "",
    ) -> dict:
        target = self._resolve_target(
            target_repo=target_repo,
            repo_path=repo_path,
            branch=branch,
            api_base=api_base,
            objective=objective,
        )
        token = self._resolve_token(target.api_base)
        existing_sha = self._existing_sha(target, token)

        body = {
            "message": commit_message.strip() or f"FORGE publish: {objective[:72]}".strip(),
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "branch": target.branch,
        }
        if existing_sha:
            body["sha"] = existing_sha

        raw = self._request_json(
            self._contents_endpoint(target),
            token=token,
            method="PUT",
            payload=body,
        )
        commit = raw.get("commit", {}) if isinstance(raw, dict) else {}
        content_block = raw.get("content", {}) if isinstance(raw, dict) else {}
        commit_sha = str(commit.get("sha", "")).strip()
        html_url = str(content_block.get("html_url") or content_block.get("download_url") or "").strip()
        return {
            "status": "completed",
            "provider": "github",
            "repository": f"{target.owner}/{target.repo}",
            "repo_path": target.path,
            "branch": target.branch,
            "commit_sha": commit_sha,
            "content_url": html_url,
            "response_status": 200,
            "published_bytes": len(content.encode("utf-8")),
            "summary": f"Published `{target.path}` to GitHub repo `{target.owner}/{target.repo}` on branch `{target.branch}`.",
            "evidence": [
                f"repo:{target.owner}/{target.repo}",
                f"path:{target.path}",
                f"branch:{target.branch}",
                f"commit:{commit_sha}" if commit_sha else "commit:unknown",
            ],
        }

    def _resolve_target(
        self,
        *,
        target_repo: str,
        repo_path: str,
        branch: str,
        api_base: str,
        objective: str,
    ) -> GitHubTarget:
        repo_value = target_repo.strip() or self._resolver.resolve(
            label="GitHub repository",
            env_names=["FORGE_GITHUB_REPOSITORY", "GITHUB_REPOSITORY", "GH_REPO"],
            soul_keys=["FORGE_GITHUB_REPOSITORY", "GITHUB_REPOSITORY", "GH_REPO", "GITHUB_REPO"],
            required=True,
        )
        owner, repo = self._parse_repo(repo_value)
        final_path = repo_path.strip().strip("/") or self._default_repo_path(objective)
        final_branch = branch.strip() or self._resolver.resolve(
            label="GitHub branch",
            env_names=["FORGE_GITHUB_BRANCH", "GITHUB_BRANCH", "GH_BRANCH"],
            soul_keys=["FORGE_GITHUB_BRANCH", "GITHUB_BRANCH", "GH_BRANCH"],
            required=False,
        ) or "main"
        final_api_base = api_base.strip() or self._resolver.resolve(
            label="GitHub API base",
            env_names=["FORGE_GITHUB_API_BASE", "GITHUB_API_BASE"],
            soul_keys=["FORGE_GITHUB_API_BASE", "GITHUB_API_BASE"],
            required=False,
        ) or "https://api.github.com"
        return GitHubTarget(
            owner=owner,
            repo=repo,
            path=final_path,
            branch=final_branch,
            api_base=final_api_base.rstrip("/"),
        )

    def _resolve_token(self, api_base: str) -> str:
        token = self._resolver.resolve(
            label="GitHub token",
            env_names=["FORGE_GITHUB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"],
            soul_keys=["FORGE_GITHUB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"],
            required=False,
        )
        if token:
            return token
        if api_base.startswith("http://127.0.0.1") or api_base.startswith("http://localhost"):
            return ""
        raise ValueError("GitHub token is not configured. Set FORGE_GITHUB_TOKEN, GITHUB_TOKEN, or GH_TOKEN.")

    @staticmethod
    def _parse_repo(value: str) -> tuple[str, str]:
        cleaned = value.strip().rstrip("/").replace(".git", "")
        if cleaned.startswith("https://github.com/"):
            parts = cleaned.removeprefix("https://github.com/").split("/")
            if len(parts) >= 2:
                return parts[0], parts[1]
        if "/" in cleaned:
            owner, repo = cleaned.split("/", 1)
            return owner.strip(), repo.strip()
        raise ValueError("GitHub repository must be `owner/repo` or a GitHub URL.")

    @staticmethod
    def _default_repo_path(objective: str) -> str:
        slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in objective[:48]).strip("-")
        slug = "-".join(part for part in slug.split("-") if part) or "forge-mission"
        return f"missions/{slug}.md"

    def _existing_sha(self, target: GitHubTarget, token: str) -> str:
        endpoint = self._contents_endpoint(target) + f"?ref={parse.quote(target.branch)}"
        try:
            data = self._request_json(endpoint, token=token, method="GET")
        except error.HTTPError as exc:
            if exc.code == 404:
                return ""
            raise
        if isinstance(data, dict):
            return str(data.get("sha", "")).strip()
        return ""

    def _contents_endpoint(self, target: GitHubTarget) -> str:
        encoded_path = parse.quote(target.path.strip("/"))
        return f"{target.api_base}/repos/{target.owner}/{target.repo}/contents/{encoded_path}"

    @staticmethod
    def _request_json(
        url: str,
        *,
        token: str,
        method: str,
        payload: dict | None = None,
    ) -> dict:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "FORGE/1.1 github-publisher",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
            headers["X-GitHub-Api-Version"] = "2022-11-28"
        req = request.Request(url, data=data, method=method.upper(), headers=headers)
        with request.urlopen(req, timeout=30) as response:
            body = response.read().decode("utf-8", errors="replace")
            return json.loads(body) if body else {}
