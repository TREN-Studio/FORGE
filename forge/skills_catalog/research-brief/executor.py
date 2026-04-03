from __future__ import annotations


def execute(payload: dict, context) -> dict:
    prompt = (
        "Create a research brief.\n"
        f"Objective: {payload['objective']}\n"
        f"User request: {payload['request']}\n"
        f"Memory context: {payload.get('memory_context', '')}\n\n"
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
