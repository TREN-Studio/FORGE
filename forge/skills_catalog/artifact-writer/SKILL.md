---
name: artifact-writer
description: Writes a generated artifact safely under the workspace artifact directory
category: operations
version: 1.0.0
---

# Purpose
Persist a generated summary, report, or draft into a safe artifact file without touching source files.

# When to use
- When the user wants to save, export, or persist output.
- When prior skills produced useful content that should be written to disk.

# When not to use
- When the user only wants a conversational answer.
- When the requested path is outside the allowed artifact root.

# Inputs
- request: raw user request
- objective: distilled objective
- prior_results: outputs from earlier skills
- memory_context: optional constraints

# Outputs
- status
- artifact_path
- bytes_written
- artifact_preview

# Execution Rules
- Write only inside the configured artifact directory.
- Prefer markdown output by default.
- Reuse prior skill output when available.

# Validation
- artifact_path must be present.
- bytes_written must be greater than zero.
- artifact_preview must not be empty.

# Safety
- Never write outside the artifact root.
- Never overwrite an existing file unless explicitly allowed by future policy.
- Never delete or mutate source code files.

# Failure Modes
- Artifact already exists.
- No usable content is available to write.
- Unsafe path requested.

# Fallback
- Return the generated content inline without writing a file.

# Response Style
Return the saved path and a short preview.
