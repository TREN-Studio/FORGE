from __future__ import annotations

import os
import re
from pathlib import Path

from forge.config.settings import OperatorSettings


KEY_VALUE_PATTERN = re.compile(r"^\s*([A-Za-z0-9_.-]+)\s*(?:=|:)\s*(.+?)\s*$")


class CredentialResolver:
    """Resolve platform configuration from env first, then SOUL.md."""

    def __init__(self, settings: OperatorSettings) -> None:
        self._settings = settings
        self._cache: dict[str, str] | None = None

    def resolve(
        self,
        *,
        label: str,
        env_names: list[str],
        soul_keys: list[str] | None = None,
        required: bool = True,
    ) -> str:
        for env_name in env_names:
            value = os.environ.get(env_name, "").strip()
            if value:
                return value

        soul_map = self._soul_map()
        for key in soul_keys or env_names:
            value = soul_map.get(key.lower().strip(), "").strip()
            if value:
                return value

        if required:
            joined = ", ".join(env_names)
            raise ValueError(f"{label} is not configured. Set one of: {joined}, or define it in SOUL.md.")
        return ""

    def _soul_map(self) -> dict[str, str]:
        if self._cache is not None:
            return self._cache

        values: dict[str, str] = {}
        for path in self._candidate_paths():
            if not path.exists():
                continue
            for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                line = raw_line.strip()
                if not line or line.startswith(("#", "-", "*", ">")):
                    continue
                match = KEY_VALUE_PATTERN.match(line)
                if not match:
                    continue
                key = match.group(1).strip().lower()
                value = match.group(2).strip().strip("`\"'")
                if value:
                    values[key] = value
        self._cache = values
        return values

    def _candidate_paths(self) -> list[Path]:
        return [
            self._settings.workspace_root / "SOUL.md",
            Path.home() / ".forge" / "runtime" / "docs" / "SOUL.md",
        ]
