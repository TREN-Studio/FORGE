---
name: research-brief
description: Produces a structured research brief for market, product, or topic discovery
category: research
version: 1.0.0
---

# Purpose
Generate a compact research artifact that turns a vague request into a decision-ready brief.

# When to use
- When the user needs discovery, investigation, comparison, or a research summary.
- When a later writing or strategy skill needs a factual brief first.

# When not to use
- When the user only wants casual conversation.
- When the request is purely file manipulation or code execution.

# Inputs
- request: the raw user request
- objective: the execution objective
- memory_context: relevant prior constraints or brand context
- prior_results: optional upstream outputs

# Outputs
- status
- brief_markdown
- key_questions

# Execution Rules
- Clarify the research objective from the request.
- Produce a concise brief with findings, assumptions, and open questions.
- Keep output structured so downstream skills can reuse it.

# Validation
- brief_markdown must not be empty.
- key_questions must contain at least one item.
- Output must directly support the stated objective.

# Safety
- Do not fabricate tool usage or external browsing.
- State assumptions clearly when evidence is limited.
- Keep claims bounded to available context.

# Failure Modes
- Request is too vague to scope.
- Model output is empty or too shallow.
- Output is not structured enough for downstream use.

# Fallback
- Fall back to a reasoning-only summary or a general analysis skill.

# Response Style
Operator brief in markdown with headings and bullet points.
