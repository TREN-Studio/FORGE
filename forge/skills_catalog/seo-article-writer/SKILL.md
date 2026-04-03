---
name: seo-article-writer
description: Produces a search-focused article draft from a user request or prior research
category: content
version: 1.0.0
---

# Purpose
Turn a topic or research brief into a structured article draft suitable for SEO-oriented publishing workflows.

# When to use
- When the user requests an article, outline, landing page, or structured content draft.
- When a research skill has already produced source context and a writing skill should convert it.

# When not to use
- When the user only needs analysis without writing.
- When the task is destructive, operational, or unrelated to content.

# Inputs
- request: the raw user request
- objective: the execution objective
- prior_results: optional upstream skill outputs
- memory_context: brand or style constraints

# Outputs
- status
- article_markdown
- publish_ready

# Execution Rules
- Use prior research if available.
- Structure the article with a title, introduction, sections, and conclusion.
- Optimize for clarity and conversion, not fluff.

# Validation
- article_markdown must not be empty.
- publish_ready must be boolean.
- Output must be coherent and usable immediately.

# Safety
- Do not invent research sources.
- Keep claims bounded to the available brief.
- Avoid hidden compliance or legal claims.

# Failure Modes
- Missing topic clarity.
- Weak or empty article structure.
- Prior results are missing or unusable.

# Fallback
- Fall back to a research brief or reasoning-only outline.

# Response Style
Clean markdown draft with clear sectioning.
