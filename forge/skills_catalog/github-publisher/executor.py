from __future__ import annotations

from forge.tools.github import GitHubPublisher


def execute(payload: dict, context) -> dict:
    content = str(payload.get("content") or _content_from_prior_results(payload.get("prior_results", {}))).strip()
    if not content:
        raise ValueError("github-publisher requires content or grounded prior results.")

    target_repo = str(payload.get("target_repo", "")).strip()
    repo_path = str(payload.get("repo_path", "")).strip()
    branch = str(payload.get("branch", "")).strip()
    commit_message = str(payload.get("commit_message", "")).strip()
    api_base = str(payload.get("api_base", "")).strip()

    if context.dry_run:
        return {
            "status": "dry_run",
            "repository": target_repo or "resolved-from-env-or-soul",
            "repo_path": repo_path or "missions/forge-mission.md",
            "branch": branch or "main",
            "response_status": 200,
            "published_bytes": len(content.encode("utf-8")),
            "summary": "Dry-run only. Planned GitHub publish was validated but not sent.",
            "evidence": [
                f"repo:{target_repo or 'resolved-from-env-or-soul'}",
                f"path:{repo_path or 'missions/forge-mission.md'}",
                f"bytes:{len(content.encode('utf-8'))}",
            ],
        }

    publisher = GitHubPublisher(context.settings)
    return publisher.publish(
        objective=str(payload.get("objective", "")),
        content=content,
        target_repo=target_repo,
        repo_path=repo_path,
        branch=branch,
        commit_message=commit_message,
        api_base=api_base,
    )


def _content_from_prior_results(prior_results: dict) -> str:
    if not isinstance(prior_results, dict):
        return ""
    for result in prior_results.values():
        if not isinstance(result, dict):
            continue
        for key in (
            "research_summary_markdown",
            "analysis_markdown",
            "file_excerpt_markdown",
            "brief_markdown",
            "article_markdown",
            "scorecard_markdown",
            "snapshot_text",
            "summary",
            "content",
        ):
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return ""
