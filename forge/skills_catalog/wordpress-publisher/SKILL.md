---
name: wordpress-publisher
description: Publishes grounded mission output to WordPress through the WP REST API
category: publishing
version: 1.0.0
grounded: true
dry_run_executor: true
---

# Purpose
Create or update WordPress content after the mission output has been validated and explicitly approved.

# When to use
- When the user explicitly asks to publish to WordPress, Hostinger WordPress, a blog post, or a page.
- When the content should be sent to a WordPress REST API endpoint.
- When the output should be grounded in prior mission evidence.

# When not to use
- When the task can stay local.
- When approval has not been granted for outbound publish actions.
- When the destination is not WordPress-compatible.

# Inputs
- request
- objective
- site_url
- title
- slug
- status
- resource_type
- resource_id
- content
- prior_results

# Outputs
- status
- site_url
- resource_type
- resource_id
- resource_status
- resource_link
- response_status
- published_bytes

# Execution Rules
- Resolve WordPress credentials from environment variables or SOUL.md only.
- Use explicit content when provided, otherwise grounded prior results.
- Publish through the WordPress REST API using authenticated HTTP requests.
- Return resource identifiers and link evidence for every successful publish.

# Validation
- response_status must indicate success.
- site_url and resource_type must be present.
- published_bytes must be greater than zero.
- resource_id should be returned by WordPress.

# Safety
- Never accept passwords from payload inputs.
- Never publish without explicit WordPress intent.
- Treat all WordPress writes as approval-gated network egress.
- Never claim a publish succeeded without an API response.

# Failure Modes
- Missing WordPress credentials
- Missing site URL
- Authentication failed
- WordPress API rejected the write

# Fallback
- Abort safely and report the exact API failure.

# Response Style
Short operator report with site, content type, status, and resource link.
