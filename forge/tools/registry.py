"""
FORGE Tool Registry
===================
Registers external tools and manages encrypted credentials storage securely.
"""

from __future__ import annotations

import json
from pathlib import Path
from cryptography.fernet import Fernet
from typing import Any

from forge.tools.base import ForgeTool


class ToolRegistry:
    """Manages available tools and local encrypted credentials."""

    def __init__(self) -> None:
        self._tools: dict[str, ForgeTool] = {}
        self._creds_path = Path.home() / ".forge" / "credentials.enc"
        self._fernet = self._init_encryption()

    def register(self, tool: ForgeTool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> ForgeTool | None:
        return self._tools.get(name)

    def all_tools(self) -> list[ForgeTool]:
        return list(self._tools.values())

    def tools_for_task(self, task: str) -> list[ForgeTool]:
        """Matches registered tools to the task prompt based on keywords."""
        task_lower = task.lower()
        matches = []
        for tool in self._tools.values():
            keywords = tool.name.replace("-", " ").split() + tool.description.lower().split()
            if any(kw in task_lower for kw in keywords if len(kw) > 2):
                matches.append(tool)
        return matches

    def set_credential(self, key: str, value: str) -> None:
        creds = self._load_creds()
        creds[key] = value
        self._save_creds(creds)

    def get_credential(self, key: str) -> str | None:
        return self._load_creds().get(key)

    def has_credential(self, tool_name: str) -> bool:
        tool = self.get(tool_name)
        if not tool:
            return False
        creds = self._load_creds()
        return all(k in creds for k in tool.requires_auth)

    def disconnect_tool(self, tool_name: str) -> None:
        tool = self.get(tool_name)
        if not tool:
            return
        creds = self._load_creds()
        for k in tool.requires_auth:
            creds.pop(k, None)
        self._save_creds(creds)

    def _init_encryption(self) -> Fernet:
        key_path = Path.home() / ".forge" / ".key"
        if key_path.exists():
            try:
                return Fernet(key_path.read_bytes())
            except Exception:
                pass
        key = Fernet.generate_key()
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.write_bytes(key)
        return Fernet(key)

    def _load_creds(self) -> dict[str, str]:
        if not self._creds_path.exists():
            return {}
        try:
            encrypted = self._creds_path.read_bytes()
            if not encrypted:
                return {}
            decrypted = self._fernet.decrypt(encrypted)
            return dict(json.loads(decrypted.decode("utf-8")))
        except Exception:
            return {}

    def _save_creds(self, creds: dict[str, str]) -> None:
        self._creds_path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(creds).encode("utf-8")
        encrypted = self._fernet.encrypt(encoded)
        self._creds_path.write_bytes(encrypted)
