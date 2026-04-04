from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from forge.tools.browser import ChromiumSemanticBrowser, URL_PATTERN, build_file_url


FILL_PATTERNS = (
    re.compile(r'fill\s+"(?P<target>.+?)"\s+with\s+"(?P<value>.+?)"', flags=re.IGNORECASE | re.DOTALL),
    re.compile(r"fill\s+`(?P<target>.+?)`\s+with\s+`(?P<value>.+?)`", flags=re.IGNORECASE | re.DOTALL),
)
CLICK_PATTERNS = (
    re.compile(r'click\s+"(?P<target>.+?)"', flags=re.IGNORECASE | re.DOTALL),
    re.compile(r"click\s+`(?P<target>.+?)`", flags=re.IGNORECASE | re.DOTALL),
)
EXTRACT_PATTERNS = (
    re.compile(r'extract\s+"(?P<target>.+?)"', flags=re.IGNORECASE | re.DOTALL),
    re.compile(r"extract\s+`(?P<target>.+?)`", flags=re.IGNORECASE | re.DOTALL),
)


def execute(payload: dict, context) -> dict:
    actions = _resolve_actions(payload, context.settings.workspace_root)
    if context.dry_run:
        return {
            "status": "dry_run",
            "summary": f"Previewed browser mission with {len(actions)} action(s).",
            "current_url": str(actions[0].get("url", "about:blank")) if actions else "about:blank",
            "title": "",
            "action_results": actions,
            "page_state": {"headings": [], "buttons": [], "inputs": [], "links": [], "text": []},
            "snapshot_text": "Dry-run only. No browser session was launched.",
            "session_isolated": True,
            "evidence": [f"planned_actions:{len(actions)}"],
        }

    browser = ChromiumSemanticBrowser(context.settings)
    result = browser.run_actions(actions)
    sanitizer = context.sanitizer
    if sanitizer:
        result["snapshot_text"] = sanitizer.sanitize_text(result["snapshot_text"], source="browser.snapshot")
        result["page_state"] = sanitizer.sanitize_value(result["page_state"], source="browser.page_state")
        result["action_results"] = sanitizer.sanitize_value(result["action_results"], source="browser.actions")
    return result


def _resolve_actions(payload: dict, workspace_root: Path) -> list[dict[str, Any]]:
    explicit_actions = payload.get("browser_actions")
    if isinstance(explicit_actions, list) and explicit_actions:
        return [dict(action) for action in explicit_actions]

    request = str(payload.get("request", ""))
    start_url = str(payload.get("start_url") or _extract_url_or_file(request, workspace_root) or "").strip()
    if start_url and not URL_PATTERN.match(start_url):
        candidate = Path(start_url)
        if not candidate.is_absolute():
            candidate = (workspace_root / candidate).resolve()
        if candidate.exists():
            start_url = build_file_url(candidate)
    if not start_url:
        raise ValueError("browser-executor requires a URL or local HTML file target.")

    actions: list[dict[str, Any]] = [{"type": "navigate", "url": start_url}]
    actions.extend(_extract_fill_actions(request))
    actions.extend(_extract_click_actions(request))
    actions.extend(_extract_extract_actions(request))

    lowered = request.lower()
    if (
        "snapshot" in lowered
        or "semantic" in lowered
        or "inspect the page" in lowered
        or "page state" in lowered
        or "browser state" in lowered
        or "browse" in lowered
        or "visit" in lowered
        or "افتح" in request
        or "صفحة" in request
        or "موقع" in request
        or not any(action["type"] == "extract" for action in actions)
    ):
        actions.append({"type": "snapshot"})

    return actions


def _extract_url_or_file(text: str, workspace_root: Path) -> str:
    match = URL_PATTERN.search(text)
    if match:
        return match.group(1)

    for token in re.findall(r"[\w./\\:-]+", text, flags=re.UNICODE):
        cleaned = token.strip("`'\" ,:;()[]{}")
        lowered = cleaned.lower()
        if lowered.endswith((".html", ".htm")):
            candidate = Path(cleaned)
            if not candidate.is_absolute():
                candidate = (workspace_root / candidate).resolve()
            if candidate.exists():
                return build_file_url(candidate)
    return ""


def _extract_fill_actions(text: str) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for pattern in FILL_PATTERNS:
        for match in pattern.finditer(text):
            actions.append(
                {
                    "type": "fill",
                    "target": {"text": match.group("target").strip()},
                    "value": match.group("value").strip(),
                }
            )
    return actions


def _extract_click_actions(text: str) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for pattern in CLICK_PATTERNS:
        for match in pattern.finditer(text):
            actions.append(
                {
                    "type": "click",
                    "target": {"text": match.group("target").strip()},
                }
            )
    return actions


def _extract_extract_actions(text: str) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for pattern in EXTRACT_PATTERNS:
        for match in pattern.finditer(text):
            actions.append(
                {
                    "type": "extract",
                    "target": {"text": match.group("target").strip()},
                }
            )
    return actions
