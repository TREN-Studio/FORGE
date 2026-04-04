from __future__ import annotations


def execute(payload: dict, context) -> dict:
    sanitizer = context.sanitizer
    safe_request = sanitizer.sanitize_text(payload["request"], source="user_request") if sanitizer else payload["request"]
    safe_memory = sanitizer.sanitize_text(payload.get("memory_context", ""), source="memory_context") if sanitizer else payload.get("memory_context", "")
    prompt = (
        "Create a research brief.\n"
        f"Objective: {payload['objective']}\n"
        f"User request: {safe_request}\n"
        f"Memory context: {safe_memory}\n\n"
        "Return markdown with sections: Findings, Assumptions, Open Questions, Recommended Next Step."
    )
    content = context.session.ask(prompt, task_type="research", remember=False)
    return {
        "status": "completed",
        "brief_markdown": content,
        "key_questions": [
            "What is the highest-value decision this brief should enable?",
            "What evidence is still missing?",
        ],
    }
