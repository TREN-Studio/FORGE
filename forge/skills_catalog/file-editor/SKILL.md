---
name: file-editor
description: Safely edits workspace files with diff visibility and strict path confinement
category: engineering
version: 1.0.0
grounded: true
dry_run_executor: true
---

# Purpose
Apply explicit file edits inside the workspace with a visible diff and strict workspace confinement.

# When to use
- When the user explicitly asks to create, write, append, prepend, or replace content in a local file.
- When a mission needs a deterministic file mutation before validation or command execution.

# When not to use
- When the request is vague and does not identify a target file or concrete content.
- When the task requires deleting files, changing permissions, or operating outside the workspace root.

# Inputs
- request: raw user request
- objective: distilled objective
- target_path: optional explicit workspace-relative path
- edit_mode: optional create, write, append, prepend, or replace
- content: optional content for create, write, append, or prepend
- find_text: optional source text for replace mode
- replace_text: optional replacement text for replace mode

# Outputs
- status
- summary
- edited_path
- operation
- changed
- created
- diff
- bytes_written

# Execution Rules
- Resolve the target path strictly inside the workspace root.
- Refuse to follow symlinks outside the workspace.
- Refuse destructive actions that are not explicit.
- Produce a unified diff for every edit.
- Support dry-run preview without writing.

# Validation
- edited_path must exist after a real write.
- diff must reflect the requested mutation when changed is true.
- bytes_written must be greater than zero for a real write.

# Safety
- No delete, move, chmod, or symlink traversal.
- No writes outside the workspace root.
- Do not invent missing content or file paths.

# Failure Modes
- Missing target path
- Missing content or replacement payload
- Replace target not found
- Path escapes workspace
- No actual change produced

# Fallback
- Fall back to file-reader or reasoning-only mode and explain why the mutation could not be applied safely.

# Response Style
Short execution report with the target file, operation, and diff status.
