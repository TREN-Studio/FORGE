---
name: github-publisher
description: Publishes grounded mission output into a GitHub repository through the Contents API
category: publishing
version: 1.0.0
grounded: true
dry_run_executor: true
---

# Purpose
Create or update repository content on GitHub after the mission output has been validated and explicitly approved.

# When to use
- When the user explicitly asks to publish, commit, or push generated output to GitHub.
- When the destination is a GitHub repository or a repo path inside a repository.
- When the content should be grounded in prior verified mission results.

# When not to use
- When the task can stay local.
- When approval has not been granted for outbound publish actions.
- When the destination is not GitHub.

# Inputs
- request
- objective
- target_repo
- repo_path
- branch
- commit_message
- content
- prior_results

# Outputs
- status
- repository
- repo_path
- branch
- commit_sha
- content_url
- response_status
- published_bytes

# Execution Rules
- Resolve GitHub credentials from environment variables or SOUL.md only.
- Use explicit content when provided, otherwise grounded prior results.
- Create or update the target file through the GitHub Contents API.
- Return commit and path evidence for every successful publish.

# Validation
- response_status must indicate success.
- repository and repo_path must be present.
- published_bytes must be greater than zero.
- commit_sha should be returned by GitHub.

# Safety
- Never accept tokens from payload inputs.
- Never publish without explicit repository intent.
- Treat all GitHub writes as approval-gated network egress.
- Never claim a commit succeeded without an API response.

# Failure Modes
- Missing GitHub token
- Missing target repository
- Invalid repository format
- GitHub API rejected the write

# Fallback
- Abort safely and report the exact API failure.

# Response Style
Short operator report with repository, path, branch, and commit reference.
