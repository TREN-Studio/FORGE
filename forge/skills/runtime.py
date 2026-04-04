from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from forge.config.settings import OperatorSettings
from forge.core.session import ForgeSession
from forge.memory.context import ContextMemory
from forge.safety.sanitizer import PromptInjectionFirewall
from forge.skills.contracts import SkillDefinition


@dataclass(slots=True)
class SkillExecutionContext:
    settings: OperatorSettings
    session: ForgeSession
    memory: ContextMemory | None = None
    dry_run: bool = False
    sanitizer: PromptInjectionFirewall | None = None
    state: dict[str, Any] = field(default_factory=dict)


class SkillRuntime:
    """Execute skills through native executors or prompt-only fallback."""

    def execute(
        self,
        skill: SkillDefinition,
        payload: dict[str, Any],
        context: SkillExecutionContext,
    ) -> Any:
        if context.dry_run and not self._supports_executor_dry_run(skill):
            return {
                "status": "dry_run",
                "skill": skill.name,
                "summary": f"Dry run for {skill.name}. No irreversible action executed.",
                "payload_preview": deepcopy(payload),
            }

        if skill.executor is not None:
            return skill.executor(payload, context)

        prompt = self._build_prompt(skill, self._sanitize_payload(payload, context))
        task_type = self._task_type(skill.category)
        content = context.session.ask(prompt, task_type=task_type, remember=False)
        return {
            "status": "completed",
            "skill": skill.name,
            "content": content,
        }

    def _build_prompt(self, skill: SkillDefinition, payload: dict[str, Any]) -> str:
        return (
            f"Skill name: {skill.name}\n"
            f"Description: {skill.description}\n"
            f"Purpose: {skill.purpose}\n"
            f"Execution rules: {'; '.join(skill.execution_rules)}\n"
            f"Validation rules: {'; '.join(skill.validation_rules)}\n"
            f"Response style: {skill.response_style}\n\n"
            f"Inputs:\n{payload}\n"
        )

    @staticmethod
    def _supports_executor_dry_run(skill: SkillDefinition) -> bool:
        return str(skill.metadata.get("dry_run_executor", "false")).lower() == "true"

    @staticmethod
    def _sanitize_payload(payload: dict[str, Any], context: SkillExecutionContext) -> dict[str, Any]:
        if context.sanitizer is None:
            return payload
        return {
            key: context.sanitizer.sanitize_value(value, source=f"skill_payload.{key}")
            for key, value in payload.items()
        }

    @staticmethod
    def _task_type(category: str) -> str:
        category = category.lower()
        if category in {"research", "analysis"}:
            return "research"
        if category in {"debugging", "engineering"}:
            return "code"
        if category in {"writing", "content", "publishing"}:
            return "creative"
        return "general"
