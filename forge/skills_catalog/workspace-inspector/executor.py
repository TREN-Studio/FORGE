from __future__ import annotations

from forge.tools.workspace import WorkspaceTools


def execute(payload: dict, context) -> dict:
    tools = WorkspaceTools(context.settings)
    summary = tools.workspace_summary()
    reviewed_files = summary["key_files"][:10]
    workspace_summary = (
        f"Workspace root: {summary['workspace_root']}\n"
        f"Artifact root: {summary['artifact_root']}\n"
        f"Files scanned: {summary['file_count']}\n"
        f"Key files: {', '.join(reviewed_files) or 'none'}\n"
        f"Top file types: {summary.get('file_types', {})}"
    )
    return {
        "status": "completed",
        "workspace_summary": workspace_summary,
        "tree": summary["tree"],
        "key_files": summary["key_files"],
        "files_reviewed": reviewed_files,
        "evidence": [f"workspace:{path}" for path in reviewed_files],
    }
