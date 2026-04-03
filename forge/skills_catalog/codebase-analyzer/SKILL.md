---
name: codebase-analyzer
description: Analyzes the local codebase using real search hits and file excerpts before producing a grounded summary
category: analysis
version: 1.0.0
grounded: true
---

# Purpose
Inspect the local project with evidence-first analysis. This skill searches real files, reads relevant excerpts, and produces a grounded technical summary instead of generic commentary.

# When to use
- When the user asks to analyze, inspect, review, explain, or audit a repo, codebase, or project.
- When the user asks in Arabic, for example: حلل المشروع، افحص الكود، اشرح هيكل هذا المسار.
- When downstream writing or planning skills need factual codebase context.

# When not to use
- When the request is unrelated to the local workspace.
- When the user wants remote web research instead of local project analysis.
- When the task is purely conversational and reasoning alone is enough.

# Inputs
- request: raw user request
- objective: distilled objective
- memory_context: optional constraints
- prior_results: previous grounded outputs if available

# Outputs
- status
- summary
- analysis_markdown
- files_reviewed
- evidence
- search_terms

# Execution Rules
- Search the local workspace before claiming findings.
- Read only files inside the workspace root.
- Prefer the smallest relevant evidence set that can support a reliable summary.
- Cite files and lines in the evidence list.

# Validation
- summary must not be empty.
- files_reviewed must contain real files.
- evidence must contain concrete search hits or file references.
- analysis_markdown must align with the user objective.

# Safety
- Read-only only.
- Never modify source files.
- Never claim a file was inspected if it was not actually read.
- Never read outside the workspace root.

# Failure Modes
- No relevant files are found.
- File reading fails.
- Search terms are too weak to produce useful evidence.

# Fallback
- Fall back to workspace-inspector when only a high-level project map is possible.
- Return a grounded partial summary if only limited evidence is available.

# Response Style
Compact engineering analysis with concrete evidence and direct next action.
