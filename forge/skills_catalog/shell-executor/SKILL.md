---
name: shell-executor
description: Runs a narrow allowlist of local workspace commands with timeout and output capture
category: operations
version: 1.0.0
grounded: true
dry_run_executor: true
---

# Purpose
Execute guarded local commands inside the workspace for validation, inspection, and deterministic engineering checks.

# When to use
- When the user explicitly asks to run a local command such as compile, test, grep, or read-only git inspection.
- When a mission needs command output as evidence after or before an edit.

# When not to use
- When the command is destructive, remote, package-installing, or ambiguous.
- When the task can be answered without running a command.

# Inputs
- request: raw user request
- objective: distilled objective
- command: optional explicit command string
- timeout_seconds: optional override within policy limits

# Outputs
- status
- summary
- command
- exit_code
- stdout
- stderr
- duration_ms

# Execution Rules
- Run only inside the workspace root.
- Use a strict allowlist of executables and subcommands.
- Capture stdout and stderr separately.
- Enforce timeout on every command.
- Support dry-run preview without execution.

# Validation
- exit_code must be zero for success unless the caller explicitly accepts non-zero status.
- stdout and stderr must be captured honestly.
- The final response must never claim success without command evidence.

# Safety
- No shell metacharacters.
- No network-capable commands.
- No package managers.
- No destructive git subcommands.

# Failure Modes
- Missing command
- Command blocked by policy
- Timeout
- Non-zero exit code
- Path escapes workspace

# Fallback
- Fall back to reasoning-only explanation and report the blocked command explicitly.

# Response Style
Short execution report with command, exit code, and the most relevant output.
