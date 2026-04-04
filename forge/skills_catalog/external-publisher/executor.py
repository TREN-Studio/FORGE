from __future__ import annotations

from forge.tools.publish import ExternalPublisher


def execute(payload: dict, context) -> dict:
    target_url = str(payload.get("target_url", "")).strip()
    if not target_url:
        raise ValueError("external-publisher requires an explicit target URL.")

    content = str(payload.get("content") or _content_from_prior_results(payload.get("prior_results", {}))).strip()
    if not content:
        raise ValueError("external-publisher requires content or grounded prior results.")

    if context.dry_run:
        return {
            "status": "dry_run",
            "target_url": target_url,
            "http_method": str(payload.get("method", "POST")).upper(),
            "published_bytes": len(content.encode("utf-8")),
            "summary": f"Dry-run only. Planned external publish to {target_url}.",
            "evidence": [f"url:{target_url}", f"bytes:{len(content.encode('utf-8'))}"],
        }

    publisher = ExternalPublisher(context.settings)
    return publisher.publish(
        target_url=target_url,
        content=content,
        objective=str(payload.get("objective", "")),
        method=str(payload.get("method", "POST")),
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
