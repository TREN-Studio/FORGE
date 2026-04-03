---
name: workspace-inspector
description: Inspects the local workspace structure and produces a deterministic project summary
category: analysis
version: 1.0.0
grounded: true
---

# Purpose
Inspect the current project workspace safely and return a structured summary based on real files.

# When to use
- When the user asks to inspect, review, summarize, or map a repo or project.
- When downstream skills need factual workspace context before writing or planning.

# When not to use
- When the request is unrelated to the local workspace.
- When the user needs remote web research rather than local project analysis.

# Inputs
- request: raw user request
- objective: distilled objective
- memory_context: optional constraints

# Outputs
- status
- workspace_summary
- tree
- key_files

# Execution Rules
- Inspect only the current workspace root.
- Ignore cache, dependency, and generated directories.
- Base the summary on real files, not assumptions.

# Validation
- workspace_summary must not be empty.
- tree must include at least one path.
- key_files must reflect actual files found.

# Safety
- Read-only only.
- Never modify project files.
- Never access paths outside the workspace root.

# Failure Modes
- Workspace root is unavailable.
- File scan fails.
- No meaningful project files are found.

# Fallback
- Fall back to a reasoning-only summary that states the inspection could not run.

# Response Style
Short factual workspace report.
