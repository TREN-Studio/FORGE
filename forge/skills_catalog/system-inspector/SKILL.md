---
name: system-inspector
description: Inspects the local computer and returns a grounded operating environment report
category: analysis
version: 1.0.0
grounded: true
---

# Purpose
Inspect the local machine safely and return a real system profile built from local runtime facts rather than model guesses.

# When to use
- When the user asks what system FORGE is running on.
- When the user asks to inspect the computer, device, workstation, host, OS, RAM, CPU, or disk state.
- When an execution plan needs verified local environment details before running heavier tasks.
- When the request is phrased in English or Arabic, for example: inspect my computer, what OS is this, افحص الحاسوب, ما نظام التشغيل, ما مواصفات هذا الجهاز.

# When not to use
- When the request is about the project workspace rather than the machine itself.
- When the user needs remote infrastructure inspection rather than the local host.
- When destructive or privileged system actions are requested.

# Inputs
- request: raw user request
- objective: distilled objective
- memory_context: optional constraints

# Outputs
- status
- summary
- facts
- analysis_markdown
- evidence

# Execution Rules
- Inspect only the local machine running FORGE.
- Use local runtime facts and safe read-only system probes.
- Return compact, decision-ready hardware and OS facts.
- Prefer deterministic data collection over generated prose.

# Validation
- facts must contain platform, hostname, python_version, memory, and disk details.
- evidence must contain factual inspection lines.
- analysis_markdown must summarize the inspected system clearly.

# Safety
- Read-only only.
- Never modify the operating system.
- Never expose secrets, tokens, browser credentials, or private file contents.
- Never claim a system fact without a collected evidence line.

# Failure Modes
- Platform-specific probe is unavailable.
- Memory or disk details cannot be collected on the current OS.
- The runtime lacks permission to read a specific safe metric.

# Fallback
- Fall back to a reduced report using platform and Python facts only.
- If inspection cannot run, return a clear grounded failure instead of guessing.

# Response Style
Short operator-grade system report with verified facts and explicit evidence.
