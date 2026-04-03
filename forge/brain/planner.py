from __future__ import annotations

from forge.brain.contracts import ExecutionPlan, PlanStep, TaskIntent
from forge.safety.guard import SafetyDecision
from forge.skills.contracts import RoutingDecision


class PlanningEngine:
    """Generate a compact execution plan for a routed task."""

    def build(
        self,
        intent: TaskIntent,
        routing: RoutingDecision,
        safety: SafetyDecision,
        max_steps: int = 5,
    ) -> ExecutionPlan:
        steps: list[PlanStep] = []

        for index, skill_name in enumerate(routing.selected_skills[:max_steps], start=1):
            fallback = routing.fallback_skills[index - 1] if index - 1 < len(routing.fallback_skills) else None
            steps.append(
                PlanStep(
                    id=f"step_{index}",
                    action=f"Execute skill `{skill_name}` to advance the objective.",
                    skill=skill_name,
                    expected_output=f"Validated output for {intent.primary_intent.value}.",
                    validation="Check schema, completeness, and alignment with the user objective.",
                    risk_note="Run in dry-run mode." if safety.use_dry_run else "",
                    fallback_skill=fallback,
                )
            )

        if not steps:
            steps.append(
                PlanStep(
                    id="step_1",
                    action="Use reasoning-only path to answer without external skill execution.",
                    skill=None,
                    expected_output="Direct, validated answer with explicit limitations.",
                    validation="Ensure the answer addresses the objective and contains no fabricated execution claims.",
                    risk_note="",
                    fallback_skill=None,
                )
            )

        return ExecutionPlan(
            objective=intent.objective,
            task_type=intent.task_type,
            risk_level=safety.risk_level,
            steps=steps,
            fallbacks=routing.fallback_skills,
            completion_criteria=[
                "Every executed step returns non-empty output.",
                "Validation passes or partial completion is reported honestly.",
                "No blocked or unsafe action is presented as complete.",
            ],
        )
