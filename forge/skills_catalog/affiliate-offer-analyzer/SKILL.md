---
name: affiliate-offer-analyzer
description: Scores an offer or product angle for affiliate and conversion potential
category: analysis
version: 1.0.0
---

# Purpose
Evaluate whether a product or offer angle is commercially worth pursuing before content or publishing effort is spent.

# When to use
- When the user needs product scoring, conversion analysis, or offer evaluation.
- When affiliate workflows need a go or no-go decision.

# When not to use
- When the task is general writing with no evaluation need.
- When destructive actions or credential handling are requested.

# Inputs
- request: the raw user request
- objective: the execution objective
- memory_context: known business constraints
- prior_results: optional upstream outputs

# Outputs
- status
- scorecard_markdown
- recommendation

# Execution Rules
- Evaluate value proposition, audience fit, conversion angle, and risk.
- Produce a scorecard and clear recommendation.
- Keep the output decision-oriented.

# Validation
- scorecard_markdown must not be empty.
- recommendation must be one of go, test, or reject.
- Output must mention reasoning for the recommendation.

# Safety
- Do not fabricate product facts.
- Keep unknowns explicit.
- Do not claim financial certainty.

# Failure Modes
- Offer context is missing.
- Recommendation cannot be justified.
- Output is vague or unstructured.

# Fallback
- Fall back to a general analysis skill or research brief.

# Response Style
Scorecard format with concise business reasoning.
