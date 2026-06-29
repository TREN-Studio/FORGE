"""
FORGE Tool Base Definitions
===========================
Base classes and results schemas for external tools integrations.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolResult:
    success: bool
    data: Any = None
    error: str | None = None
    metadata: dict = field(default_factory=dict)
    action_taken: str = ""


class ForgeTool(ABC):
    name: str = ""
    description: str = ""
    risk_class: str = "read"  # read | write | publish | payment | admin
    requires_auth: list[str] = []
    available_actions: list[str] = []

    @abstractmethod
    async def execute(self, action: str, params: dict[str, Any]) -> ToolResult:
        """Executes a specific action on this tool integration."""
        pass

    def is_destructive(self, action: str) -> bool:
        destructive = ["delete", "send_email", "publish_public", "payment", "post", "send"]
        return action.lower() in destructive

    def needs_confirmation(self, action: str) -> bool:
        return self.is_destructive(action)

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "risk_class": self.risk_class,
            "actions": self.available_actions,
            "requires_auth": self.requires_auth,
        }
