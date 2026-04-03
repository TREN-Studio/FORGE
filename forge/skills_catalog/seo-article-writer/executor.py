from __future__ import annotations


def execute(payload: dict, context) -> dict:
    prior_results = payload.get("prior_results", {})
    source_context = prior_results.get("research-brief", "")
    prompt = (
        "Write a structured SEO article draft.\n"
        f"Objective: {payload['objective']}\n"
        f"User request: {payload['request']}\n"
        f"Source context: {source_context}\n"
        f"Memory context: {payload.get('memory_context', '')}\n\n"
        "Return markdown with: Title, Hook, Main Sections, Conversion Section, Conclusion."
    )
    content = context.session.ask(prompt, task_type="creative", remember=False)
    return {
        "status": "completed",
        "article_markdown": content,
        "publish_ready": True,
    }
