from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable


class SkillSource(str, Enum):
    BUNDLED = "bundled"
    EXTERNAL = "external"


@dataclass(slots=True)
class SkillDefinition:
    name: str
    description: str
    category: str
    version: str
    path: Path
    purpose: str
    when_to_use: list[str]
    when_not_to_use: list[str]
    inputs: list[str]
    outputs: list[str]
    execution_rules: list[str]
    validation_rules: list[str]
    safety_rules: list[str]
    failure_modes: list[str]
    fallback_notes: list[str]
    response_style: str
    source: SkillSource
    trusted: bool
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    executor: Callable[[dict[str, Any], Any], Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def searchable_text(self) -> str:
        parts = [
            self.name,
            self.description,
            self.category,
            self.purpose,
            " ".join(self.when_to_use),
            " ".join(self.inputs),
            " ".join(self.outputs),
            " ".join(self.execution_rules),
            " ".join(self.validation_rules),
            " ".join(self.safety_rules),
        ]
        return " ".join(parts).lower()


@dataclass(slots=True)
class SkillMatch:
    skill_name: str
    score: float
    reasons: list[str]


@dataclass(slots=True)
class RoutingDecision:
    mode: str
    selected_skills: list[str]
    fallback_skills: list[str]
    matches: list[SkillMatch]
    reasons: list[str]
