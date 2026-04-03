from __future__ import annotations

import hashlib
import re

from forge.tools.workspace import WorkspaceTools


def execute(payload: dict, context) -> dict:
    tools = WorkspaceTools(context.settings)
    content = _artifact_content(payload)
    if not content.strip():
        raise ValueError("No usable content available for artifact writing.")

    target = _write_unique_artifact(tools, payload["objective"], content)
    return {
        "status": "completed",
        "artifact_path": str(target),
        "bytes_written": target.stat().st_size,
        "artifact_preview": content[:500],
    }


def _artifact_name(objective: str, suffix: int | None = None) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", objective.lower()).strip("-")
    if not slug:
        slug = f"artifact-{hashlib.sha1(objective.encode('utf-8')).hexdigest()[:8]}"
    slug = slug[:80]
    if suffix and suffix > 1:
        return f"{slug}-{suffix}.md"
    return f"{slug}.md"


def _write_unique_artifact(tools: WorkspaceTools, objective: str, content: str):
    for suffix in range(1, 100):
        filename = _artifact_name(objective, suffix=suffix)
        try:
            return tools.write_artifact(filename, content, overwrite=False)
        except FileExistsError:
            continue
    raise FileExistsError("Unable to allocate a unique artifact filename.")


def _artifact_content(payload: dict) -> str:
    prior_results = payload.get("prior_results", {})
    lines = ["# FORGE Artifact", "", f"Objective: {payload['objective']}", ""]
    for skill_name, result in prior_results.items():
        lines.append(f"## {skill_name}")
        if isinstance(result, dict):
            if "workspace_summary" in result:
                lines.append(result["workspace_summary"])
            elif "analysis_markdown" in result:
                lines.append(result["analysis_markdown"])
            elif "file_excerpt_markdown" in result:
                lines.append(result["file_excerpt_markdown"])
            elif "brief_markdown" in result:
                lines.append(result["brief_markdown"])
            elif "article_markdown" in result:
                lines.append(result["article_markdown"])
            elif "scorecard_markdown" in result:
                lines.append(result["scorecard_markdown"])
            elif "summary" in result:
                lines.append(result["summary"])
            elif "content" in result:
                lines.append(result["content"])
            else:
                lines.append(str({k: v for k, v in result.items() if k != "payload_preview"}))
        else:
            lines.append(str(result))
        lines.append("")

    if len(lines) <= 4:
        lines.append(payload["request"])
    return "\n".join(lines).strip() + "\n"
