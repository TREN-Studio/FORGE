from __future__ import annotations

import re

from forge.tools.workspace import WorkspaceTools


FILE_HINTS = (".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".md", ".toml", ".yaml", ".yml", ".sql", "/")


def execute(payload: dict, context) -> dict:
    tools = WorkspaceTools(context.settings)
    files = _resolve_files(payload, tools)
    if not files:
        raise ValueError("No readable workspace file could be resolved from the request.")

    excerpts = [tools.read_excerpt(path, start_line=1, end_line=120) for path in files[:4]]
    evidence = [
        f"{excerpt['path']}:{excerpt['start_line']}-{excerpt['end_line']} | excerpt collected"
        for excerpt in excerpts
    ]
    markdown = _format_excerpts(excerpts)
    summary = f"Collected grounded excerpts from {len(excerpts)} file(s): {', '.join(files[:4])}."
    return {
        "status": "completed",
        "summary": summary,
        "file_excerpt_markdown": markdown,
        "files_reviewed": files[:4],
        "evidence": evidence,
    }


def _resolve_files(payload: dict, tools: WorkspaceTools) -> list[str]:
    request = str(payload.get("request", ""))
    candidates = _extract_paths(request)

    prior_results = payload.get("prior_results", {})
    for result in prior_results.values():
        if isinstance(result, dict):
            for path in result.get("files_reviewed", []):
                candidates.append(path)

    valid: list[str] = []
    seen: set[str] = set()
    for path in candidates:
        normalized = path.replace("\\", "/").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        try:
            tools.read_excerpt(normalized, start_line=1, end_line=10)
            valid.append(normalized)
        except Exception:
            continue
    return valid


def _extract_paths(text: str) -> list[str]:
    paths: list[str] = []
    for token in re.findall(r"[\w./\\:-]+", text, flags=re.UNICODE):
        lowered = token.lower()
        if not any(hint in lowered for hint in FILE_HINTS):
            continue
        cleaned = token.strip("`'\" ,:;()[]{}")
        if len(cleaned) < 3:
            continue
        if cleaned.startswith(("http://", "https://")):
            continue
        paths.append(cleaned.replace("\\", "/"))
    return list(dict.fromkeys(paths))


def _format_excerpts(excerpts: list[dict]) -> str:
    blocks: list[str] = []
    for excerpt in excerpts:
        blocks.append(
            f"## {excerpt['path']}:{excerpt['start_line']}-{excerpt['end_line']}\n"
            "```text\n"
            f"{excerpt['content']}\n"
            "```"
        )
    return "\n\n".join(blocks)
