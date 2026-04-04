from __future__ import annotations


def execute(payload: dict, context) -> dict:
    sanitizer = context.sanitizer
    safe_request = sanitizer.sanitize_text(payload["request"], source="user_request") if sanitizer else payload["request"]
    safe_memory = sanitizer.sanitize_text(payload.get("memory_context", ""), source="memory_context") if sanitizer else payload.get("memory_context", "")
    prompt = (
        "Analyze an affiliate offer.\n"
        f"Objective: {payload['objective']}\n"
        f"User request: {safe_request}\n"
        f"Memory context: {safe_memory}\n\n"
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
