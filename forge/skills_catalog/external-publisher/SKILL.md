---
name: external-publisher
description: Publishes structured content to an external HTTP endpoint with explicit approval and evidence
category: operations
version: 1.0.0
grounded: true
dry_run_executor: true
---

# Purpose
Send a deliberate, structured payload to an external endpoint after the mission is validated and explicitly approved.

# When to use
- When the user explicitly asks to publish, send, post, upload, or submit output outside the local machine.
- When a mission needs to deliver an artifact to a webhook or external HTTP endpoint.

# When not to use
- When the task can remain local.
- When approval has not been granted for network egress.
- When credentials or authenticated browser sessions are required.

# Inputs
- request
- objective
- target_url
- method
- content
- prior_results

# Outputs
- status
- target_url
- response_status
- response_body_preview
- published_bytes

# Execution Rules
- Send a structured JSON payload to the target endpoint.
- Use explicit request content or grounded prior results as the publish body.
- Return HTTP evidence for every publish attempt.

# Validation
- response_status must indicate success.
- published_bytes must be greater than zero.
- target_url must match the requested destination.

# Safety
- Never publish without an explicit target URL.
- Never publish silently.
- Treat all external network egress as approval-gated.
- Never claim a publish succeeded without an HTTP response.

# Failure Modes
- Missing target URL
- Empty content
- HTTP request failed
- Endpoint returned an error

# Fallback
- Abort safely and report the exact publish failure.

# Response Style
Short operator report with target URL, HTTP status, and published byte count.
