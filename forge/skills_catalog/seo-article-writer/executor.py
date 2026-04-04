from __future__ import annotations


def execute(payload: dict, context) -> dict:
    prior_results = payload.get("prior_results", {})
    source_context = prior_results.get("research-brief", "")
    sanitizer = context.sanitizer
    safe_request = sanitizer.sanitize_text(payload["request"], source="user_request") if sanitizer else payload["request"]
    safe_source = sanitizer.sanitize_value(source_context, source="prior_results.research-brief") if sanitizer else source_context
    safe_memory = sanitizer.sanitize_text(payload.get("memory_context", ""), source="memory_context") if sanitizer else payload.get("memory_context", "")
    prompt = (
        "Write a structured SEO article draft.\n"
        f"Objective: {payload['objective']}\n"
        f"User request: {safe_request}\n"
        f"Source context: {safe_source}\n"
        f"Memory context: {safe_memory}\n\n"
        "Return markdown with: Title, Hook, Main Sections, Conversion Section, Conclusion."
    )
    content = context.session.ask(prompt, task_type="creative", remember=False)
    return {
        "status": "completed",
        "article_markdown": content,
        "publish_ready": True,
    }
