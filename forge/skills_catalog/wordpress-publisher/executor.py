from __future__ import annotations

from forge.tools.wordpress import WordPressPublisher


def execute(payload: dict, context) -> dict:
    content = str(payload.get("content") or _content_from_prior_results(payload.get("prior_results", {}))).strip()
    if not content:
        raise ValueError("wordpress-publisher requires content or grounded prior results.")

    site_url = str(payload.get("site_url", "")).strip()
    title = str(payload.get("title", "")).strip()
    slug = str(payload.get("slug", "")).strip()
    status = str(payload.get("status", "")).strip()
    resource_type = str(payload.get("resource_type", "")).strip()
    resource_id = str(payload.get("resource_id", "")).strip()

    if context.dry_run:
        return {
            "status": "dry_run",
            "site_url": site_url or "resolved-from-env-or-soul",
            "resource_type": resource_type or "posts",
            "resource_id": resource_id,
            "resource_status": status or "publish",
            "response_status": 200,
            "published_bytes": len(content.encode("utf-8")),
            "summary": "Dry-run only. Planned WordPress publish was validated but not sent.",
            "evidence": [
                f"site:{site_url or 'resolved-from-env-or-soul'}",
                f"type:{resource_type or 'posts'}",
                f"bytes:{len(content.encode('utf-8'))}",
            ],
        }

    publisher = WordPressPublisher(context.settings)
    return publisher.publish(
        objective=str(payload.get("objective", "")),
        content=content,
        site_url=site_url,
        title=title,
        slug=slug,
        status=status,
        resource_type=resource_type,
        resource_id=resource_id,
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
