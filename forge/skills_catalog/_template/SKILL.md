---
name: skill-template
description: Template contract for new FORGE skills
category: template
version: 1.0.0
---

# Purpose
Define a reusable skill contract without touching the brain.

# When to use
- When you need to add a new specialized capability to FORGE.

# When not to use
- When the request can be handled safely through reasoning only.

# Inputs
- request: the raw user request
- objective: the distilled task objective
- prior_results: optional outputs from previous skills

# Outputs
- structured result payload
- execution status

# Execution Rules
- Accept normalized operator payloads.
- Prefer deterministic outputs when possible.
- Keep side effects explicit.

# Validation
- Return the required fields.
- Do not return empty content.
- Match the declared schema when a schema exists.

# Safety
- Declare destructive behavior explicitly.
- Reject unsafe execution by default.
- Never expose secrets.

# Failure Modes
- Missing input
- Dependency not available
- Validation failure

# Fallback
- Return a structured failure and allow the operator to choose a fallback skill.

# Response Style
Short, structured, and production-ready.
