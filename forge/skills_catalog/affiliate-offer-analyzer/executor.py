from __future__ import annotations


def execute(payload: dict, context) -> dict:
    prompt = (
        "Analyze an affiliate offer.\n"
        f"Objective: {payload['objective']}\n"
        f"User request: {payload['request']}\n"
        f"Memory context: {payload.get('memory_context', '')}\n\n"
        "Return markdown with: Offer Summary, Conversion Upside, Risks, Recommendation."
    )
    content = context.session.ask(prompt, task_type="research", remember=False)
    recommendation = "test"
    lowered = content.lower()
    if "recommendation: go" in lowered or "recommendation - go" in lowered:
        recommendation = "go"
    elif "recommendation: reject" in lowered or "recommendation - reject" in lowered:
        recommendation = "reject"
    return {
        "status": "completed",
        "scorecard_markdown": content,
        "recommendation": recommendation,
    }
