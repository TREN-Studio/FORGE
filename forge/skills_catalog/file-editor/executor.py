from __future__ import annotations

import re

from forge.tools.workspace import WorkspaceTools


FILE_HINTS = ("/", "\\", ".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".md", ".txt", ".toml", ".yaml", ".yml", ".sql")
CONTENT_BOUNDARY_PREFIXES = (
    "then ",
    "then run",
    "then execute",
    "and then",
    "next ",
    "after that",
    "afterwards",
    "finally",
    "now ",
    "run ",
    "execute ",
    "compile ",
    "test ",
    "push ",
    "publish ",
    "ثم",
    "بعد ذلك",
    "بعدها",
    "اخيرا",
    "أخيراً",
    "الآن",
    "نفذ",
    "شغل",
)


def execute(payload: dict, context) -> dict:
    tools = WorkspaceTools(context.settings)
    spec = _resolve_edit_spec(payload)
    preview = tools.preview_text_edit(
        relative_path=spec["target_path"],
        mode=spec["mode"],
        content=spec.get("content"),
        find_text=spec.get("find_text"),
        replace_text=spec.get("replace_text"),
    )
    evidence = [
        f"path:{preview['path']}",
        f"operation:{preview['mode']}",
        f"created:{preview['created']}",
        f"changed:{preview['changed']}",
    ]
    summary = (
        f"{'Previewed' if context.dry_run else 'Applied'} {preview['mode']} on `{preview['path']}`. "
        f"{'Change detected.' if preview['changed'] else 'No textual change was produced.'}"
    )

    if context.dry_run:
        return {
            "status": "dry_run",
            "summary": summary,
            "edited_path": preview["path"],
            "operation": preview["mode"],
            "changed": preview["changed"],
            "created": preview["created"],
            "diff": preview["diff"],
            "bytes_written": preview["bytes_after"],
            "content_preview": (spec.get("content") or spec.get("replace_text") or "")[:400],
            "evidence": evidence,
        }

    applied = tools.apply_text_edit(
        relative_path=spec["target_path"],
        mode=spec["mode"],
        content=spec.get("content"),
        find_text=spec.get("find_text"),
        replace_text=spec.get("replace_text"),
    )
    return {
        "status": "completed",
        "summary": summary,
        "edited_path": applied["path"],
        "operation": applied["mode"],
        "changed": applied["changed"],
        "created": applied["created"],
        "diff": applied["diff"],
        "bytes_written": applied["bytes_written"],
        "rollback": applied.get("rollback"),
        "evidence": evidence,
    }


def _resolve_edit_spec(payload: dict) -> dict[str, str]:
    request = str(payload.get("request", ""))
    target_path = str(payload.get("target_path") or _extract_path(request) or "").strip()
    if not target_path:
        raise ValueError("file-editor requires an explicit target path.")

    mode = str(payload.get("edit_mode") or _infer_mode(request)).strip().lower()
    if not mode:
        raise ValueError("file-editor could not infer an edit mode.")

    spec = {
        "target_path": target_path.replace("\\", "/"),
        "mode": mode,
    }

    if mode == "replace":
        replace_payload = _extract_replace_payload(payload, request)
        spec.update(replace_payload)
    else:
        content = str(payload.get("content") or _extract_content(request) or "")
        if not content:
            raise ValueError("file-editor requires explicit content for this edit.")
        spec["content"] = content
    return spec


def _infer_mode(request: str) -> str:
    lowered = request.lower()
    if any(token in lowered for token in ("append", "add to the end", "append to", "اضف", "الحق")):
        return "append"
    if any(token in lowered for token in ("prepend", "add to the beginning")):
        return "prepend"
    if any(token in lowered for token in ("replace", "swap", "بدل")):
        return "replace"
    if any(token in lowered for token in ("create", "new file", "انشئ", "انشء")):
        return "create"
    if any(token in lowered for token in ("write", "save", "update", "modify", "edit", "patch", "اكتب", "حدث", "حرر")):
        return "write"
    return ""


def _extract_path(text: str) -> str:
    for token in re.findall(r"[\w./\\:-]+", text, flags=re.UNICODE):
        cleaned = token.strip("`'\" ,:;()[]{}")
        lowered = cleaned.lower()
        if len(cleaned) < 3:
            continue
        if cleaned.startswith(("http://", "https://")):
            continue
        if any(hint in lowered for hint in FILE_HINTS):
            return cleaned
    return ""


def _extract_content(text: str) -> str:
    blocks = _code_blocks(text)
    if blocks:
        return blocks[-1].strip()

    match = re.search(r"(?:(?:exactly|only)\s+this\s+)?(?:content|text)\s*:\s*", text, flags=re.IGNORECASE)
    if match:
        remainder = text[match.end() :]
        collected: list[str] = []
        for raw_line in remainder.splitlines():
            line = raw_line.rstrip()
            stripped = line.strip()
            lowered = stripped.lower()
            if collected and any(lowered.startswith(prefix) for prefix in CONTENT_BOUNDARY_PREFIXES):
                break
            collected.append(line)
        while collected and not collected[-1].strip():
            collected.pop()
        return _trim_inline_content_boundary("\n".join(collected).strip())
    return ""


def _trim_inline_content_boundary(content: str) -> str:
    if not content:
        return ""
    boundary = re.search(
        r"(?i)(?:(?<=[.!?])\s+|\n+)\b("
        r"then\s+run|then\s+execute|then|and\s+then|next|after\s+that|afterwards|finally|now|"
        r"run|execute|compile|test|push|publish|ثم|بعد\s+ذلك|بعدها|اخيرا|أخيراً|الآن|نفذ|شغل"
        r")\b",
        content,
    )
    if not boundary:
        return content
    return content[: boundary.start()].rstrip()


def _extract_replace_payload(payload: dict, request: str) -> dict[str, str]:
    find_text = payload.get("find_text")
    replace_text = payload.get("replace_text")
    if isinstance(find_text, str) and isinstance(replace_text, str):
        return {
            "find_text": find_text,
            "replace_text": replace_text,
        }

    blocks = _code_blocks(request)
    if len(blocks) >= 2:
        return {
            "find_text": blocks[0].strip(),
            "replace_text": blocks[1].strip(),
        }

    quoted = re.search(
        r'replace\s+"(?P<old>.+?)"\s+with\s+"(?P<new>.+?)"',
        request,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if quoted:
        return {
            "find_text": quoted.group("old"),
            "replace_text": quoted.group("new"),
        }
    raise ValueError("Replace mode requires explicit find_text and replace_text.")


def _code_blocks(text: str) -> list[str]:
    return re.findall(r"```(?:[\w.+-]+)?\n(.*?)```", text, flags=re.DOTALL)
