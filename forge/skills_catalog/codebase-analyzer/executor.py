from __future__ import annotations

import re

from forge.tools.workspace import WorkspaceTools


STOP_WORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "this",
    "that",
    "into",
    "then",
    "give",
    "need",
    "want",
    "show",
    "analyze",
    "inspect",
    "review",
    "explain",
    "repo",
    "project",
    "codebase",
    "file",
    "files",
    "path",
    "حول",
    "هذا",
    "هذه",
    "حلل",
    "افحص",
    "راجع",
    "اشرح",
    "مشروع",
    "ملف",
    "مسار",
}


def execute(payload: dict, context) -> dict:
    tools = WorkspaceTools(context.settings)
    workspace = tools.workspace_summary()
    search_terms = _search_terms(payload)
    hits = tools.search_text(" ".join(search_terms), max_hits=16)
    files_reviewed = _select_files(hits, workspace["key_files"])
    excerpts = _build_excerpts(tools, hits, files_reviewed)
    evidence = _evidence_lines(hits, excerpts)
    analysis = _build_analysis_markdown(
        payload=payload,
        workspace=workspace,
        search_terms=search_terms,
        hits=hits,
        excerpts=excerpts,
    )

    summary = (
        f"Reviewed {len(files_reviewed)} files using search terms {', '.join(search_terms[:6]) or 'none'}. "
        f"Captured {len(evidence)} evidence points from the local workspace."
    )
    return {
        "status": "completed" if files_reviewed else "partial",
        "summary": summary,
        "analysis_markdown": analysis,
        "files_reviewed": files_reviewed,
        "evidence": evidence,
        "search_terms": search_terms,
    }


def _search_terms(payload: dict) -> list[str]:
    source = " ".join(
        filter(
            None,
            [
                payload.get("request", ""),
                payload.get("objective", ""),
            ],
        )
    )
    terms: list[str] = []
    for token in re.findall(r"[\w./\\-]+", source.lower(), flags=re.UNICODE):
        cleaned = token.replace("\\", "/").strip("./-")
        if len(cleaned) < 3 or cleaned in STOP_WORDS:
            continue
        terms.append(cleaned)
    return list(dict.fromkeys(terms))[:8]


def _select_files(hits: list[dict], key_files: list[str], limit: int = 5) -> list[str]:
    selected: list[str] = []
    seen: set[str] = set()
    for hit in hits:
        path = hit["path"]
        if path in seen:
            continue
        seen.add(path)
        selected.append(path)
        if len(selected) >= limit:
            return selected
    for path in key_files:
        if path in seen:
            continue
        seen.add(path)
        selected.append(path)
        if len(selected) >= limit:
            break
    return selected


def _build_excerpts(tools: WorkspaceTools, hits: list[dict], files_reviewed: list[str]) -> list[dict]:
    hit_by_file: dict[str, dict] = {}
    for hit in hits:
        hit_by_file.setdefault(hit["path"], hit)

    excerpts: list[dict] = []
    for path in files_reviewed:
        hit = hit_by_file.get(path)
        if hit is not None:
            start_line = max(1, hit["line"] - 8)
            end_line = hit["line"] + 24
        else:
            start_line = 1
            end_line = 80
        excerpts.append(tools.read_excerpt(path, start_line=start_line, end_line=end_line))
    return excerpts


def _evidence_lines(hits: list[dict], excerpts: list[dict]) -> list[str]:
    evidence = [
        f"{hit['path']}:{hit['line']} | {hit['text']}"
        for hit in hits[:10]
    ]
    for excerpt in excerpts:
        evidence.append(
            f"{excerpt['path']}:{excerpt['start_line']}-{excerpt['end_line']} | excerpt collected"
        )
    return list(dict.fromkeys(evidence))[:12]


def _format_evidence(evidence: list[str]) -> str:
    return "\n".join(f"- {line}" for line in evidence) or "- no evidence"


def _format_excerpts(excerpts: list[dict]) -> str:
    blocks: list[str] = []
    for excerpt in excerpts:
        blocks.append(
            f"## {excerpt['path']}:{excerpt['start_line']}-{excerpt['end_line']}\n"
            f"{excerpt['content']}"
        )
    return "\n\n".join(blocks) or "No excerpts collected."


def _build_analysis_markdown(
    payload: dict,
    workspace: dict,
    search_terms: list[str],
    hits: list[dict],
    excerpts: list[dict],
) -> str:
    strongest_hits = hits[:5]
    relevant_files = [excerpt["path"] for excerpt in excerpts]

    finding_lines = [
        f"- Workspace scanned: `{workspace['workspace_root']}`",
        f"- File count: `{workspace['file_count']}`",
        f"- Top file types: `{workspace.get('file_types', {})}`",
        f"- Search terms used: `{', '.join(search_terms) or 'none'}`",
    ]
    if strongest_hits:
        finding_lines.append(
            f"- Strongest hit: `{strongest_hits[0]['path']}:{strongest_hits[0]['line']}` -> {strongest_hits[0]['text']}"
        )
    else:
        finding_lines.append("- No direct text hits were found; analysis fell back to key project files.")

    file_lines = []
    for excerpt in excerpts:
        file_lines.append(f"- `{excerpt['path']}:{excerpt['start_line']}-{excerpt['end_line']}`")

    risk_lines = []
    if not strongest_hits:
        risk_lines.append("- Search evidence was weak, so conclusions are based on fallback key files only.")
    if len(relevant_files) <= 2:
        risk_lines.append("- Coverage is narrow; inspect additional files before making architectural changes.")
    if not risk_lines:
        risk_lines.append("- No immediate execution blockers were detected in the sampled evidence.")

    if payload.get("requested_output") == "file":
        next_action = "- Persist this grounded analysis with `artifact-writer`."
    else:
        next_action = "- Continue with the highest-signal file listed above before changing code."

    lines = [
        "# Objective",
        payload["objective"],
        "",
        "# Findings",
        *finding_lines,
        "",
        "# Relevant Files",
        *(file_lines or ["- No files were reviewed."]),
        "",
        "# Risks or Gaps",
        *risk_lines,
        "",
        "# Best Next Action",
        next_action,
    ]
    return "\n".join(lines)
