---
name: file-reader
description: Reads explicit local files safely and returns grounded excerpts for inspection tasks
category: analysis
version: 1.0.0
grounded: true
---

# Purpose
Read one or more explicit files from the local workspace and return inspectable excerpts without modifying anything.

# When to use
- When the user asks to read, inspect, review, or explain a specific file or path.
- When the request includes a path such as forge/brain/operator.py or pyproject.toml.
- When the user asks in Arabic, for example: اقرأ هذا الملف، راجع هذا المسار، اشرح هذا الملف.

# When not to use
- When the user wants a high-level repo-wide analysis instead of specific file inspection.
- When the requested path is outside the workspace root.

# Inputs
- request: raw user request
- objective: distilled objective
- prior_results: optional earlier outputs

# Outputs
- status
- summary
- file_excerpt_markdown
- files_reviewed
- evidence

# Execution Rules
- Read only files inside the workspace root.
- Prefer explicit paths from the request.
- If no explicit path exists, use grounded prior results rather than guessing.
- Keep excerpts compact and inspectable.

# Validation
- files_reviewed must not be empty.
- evidence must contain file references.
- file_excerpt_markdown must not be empty.

# Safety
- Read-only only.
- Never modify files.
- Never access paths outside the workspace root.
- Never fabricate a path.

# Failure Modes
- No valid file path is found.
- The file does not exist.
- File reading fails.

# Fallback
- Fall back to codebase-analyzer if the request is broader than a single file.
- Return a grounded failure if no explicit file can be resolved.

# Response Style
Short inspection output with file references and direct excerpts.
