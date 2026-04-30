from __future__ import annotations

import json
import re
from typing import Any


class JSONValidationError(ValueError):
    """Raised when structured JSON output cannot be parsed or repaired."""


def validate_json_strict(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise JSONValidationError(str(exc)) from exc


def auto_repair_json(text: str) -> str:
    candidate = text.strip()
    if not candidate:
        raise JSONValidationError("JSON content is empty.")

    candidate = candidate.replace('\\"', '"')
    candidate = candidate.replace("'", '"')
    candidate = re.sub(r"([{,]\s*)([A-Za-z_][A-Za-z0-9_-]*)(\s*:)", r'\1"\2"\3', candidate)
    candidate = re.sub(r",\s*([}\]])", r"\1", candidate)
    candidate = _quote_bare_values(candidate)

    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise JSONValidationError(f"JSON repair failed: {exc}") from exc
    return json.dumps(parsed, ensure_ascii=False, indent=2)


def ensure_valid_json_text(text: str) -> tuple[str, bool]:
    try:
        parsed = validate_json_strict(text)
        return json.dumps(parsed, ensure_ascii=False, indent=2), False
    except JSONValidationError:
        return auto_repair_json(text), True


def _quote_bare_values(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        prefix = match.group("prefix")
        raw_value = match.group("value").strip()
        suffix = match.group("suffix")
        lowered = raw_value.lower()
        if (
            raw_value.startswith(('"', "{", "["))
            or lowered in {"true", "false", "null"}
            or re.fullmatch(r"-?\d+(?:\.\d+)?", raw_value)
        ):
            return f"{prefix}{raw_value}{suffix}"
        escaped = raw_value.replace('"', '\\"')
        return f'{prefix}"{escaped}"{suffix}'

    return re.sub(
        r"(?P<prefix>:\s*)(?P<value>[^,\}\]\n]+?)(?P<suffix>\s*[,}\]])",
        replace,
        text,
    )
