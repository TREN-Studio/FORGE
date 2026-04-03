from __future__ import annotations

from typing import Any

from forge.brain.composer import ResponseComposer
from forge.brain.contracts import CompletionState, OperatorResult, StepExecutionResult
from forge.brain.intent import IntentResolver
from forge.brain.planner import PlanningEngine
from forge.brain.prompt import CORE_BRAIN_PROMPT
from forge.config.settings import OperatorSettings
from forge.core.session import ForgeSession
from forge.memory.context import ContextMemory
from forge.recovery.manager import RecoveryManager
from forge.safety.guard import SafetyDecision, SafetyGuard
from forge.skills.registry import SkillRegistry
from forge.skills.router import SkillRouter
from forge.skills.runtime import SkillExecutionContext, SkillRuntime
from forge.validation.validator import ResultValidator


class ForgeOperator:
    """Skill-based autonomous operator built on top of FORGE."""

    def __init__(
        self,
        settings: OperatorSettings | None = None,
        session: ForgeSession | None = None,
    ) -> None:
        self.settings = settings or OperatorSettings()
        self.session = session or ForgeSession(
            system_prompt=CORE_BRAIN_PROMPT,
            memory=self.settings.enable_memory,
        )
        self.memory = ContextMemory(self.session._memory) if self.session._memory else None
        self.registry = SkillRegistry(self.settings)
        self.registry.refresh()
        self.intent_resolver = IntentResolver()
        self.skill_router = SkillRouter(self.settings)
        self.safety_guard = SafetyGuard(self.settings)
        self.planner = PlanningEngine()
        self.runtime = SkillRuntime()
        self.validator = ResultValidator()
        self.recovery = RecoveryManager(max_retries_per_step=self.settings.max_retries_per_step)
        self.composer = ResponseComposer()

    def handle(
        self,
        request: str,
        confirmed: bool = False,
        dry_run: bool = False,
    ) -> OperatorResult:
        memory_context = self.memory.build_context(request, self.settings.memory_recall_limit) if self.memory else ""
        intent = self.intent_resolver.resolve(request, memory_context=memory_context)
        skills = self.registry.list()
        routing = self.skill_router.route(intent, skills)
        routing.selected_skills = self._ordered_skill_names(routing.selected_skills)
        skill_lookup = {skill.name: skill for skill in skills}
        safety = self.safety_guard.evaluate(
            request=request,
            intent=intent,
            routing=routing,
            skill_lookup=skill_lookup,
            confirmed=confirmed,
            dry_run_requested=dry_run,
        )
        plan = self.planner.build(intent, routing, safety, max_steps=self.settings.max_plan_steps)

        if safety.blocked:
            status = CompletionState.NEEDS_HUMAN_CONFIRMATION if safety.requires_confirmation else CompletionState.FAILED
            return OperatorResult(
                objective=intent.objective,
                approach_taken=[
                    "Resolved intent.",
                    "Selected skills.",
                    "Blocked execution in SafetyGuard.",
                ],
                result="Execution blocked before any skill ran.",
                validation_status=status,
                risks_or_limitations=safety.reasons or ["Execution blocked by policy."],
                best_next_action=self.composer.best_next_action(status),
                intent=intent,
                plan=plan,
                step_results=[],
                artifacts={},
            )

        runtime_context = SkillExecutionContext(
            settings=self.settings,
            session=self.session,
            memory=self.memory,
            dry_run=safety.use_dry_run,
            state={"memory_context": memory_context},
        )

        step_results: list[StepExecutionResult] = []
        artifacts: dict[str, Any] = {}
        prior_results: dict[str, Any] = {}

        for step in plan.steps:
            if step.skill is None:
                reasoning_output = self.session.ask(request, task_type=intent.task_type, remember=False)
                validation = self.validator.validate_step(None, reasoning_output, step.expected_output, request)
                step_results.append(
                    StepExecutionResult(
                        step_id=step.id,
                        skill=None,
                        status=validation.status,
                        output=reasoning_output,
                        evidence=[],
                        validation_status=validation.status,
                        validation_notes=validation.notes,
                        attempts=1,
                    )
                )
                artifacts["reasoning"] = reasoning_output
                break

            current_skill = self.registry.get(step.skill)
            if current_skill is None:
                step_results.append(
                    StepExecutionResult(
                        step_id=step.id,
                        skill=step.skill,
                        status=CompletionState.FAILED,
                        output=None,
                        validation_status=CompletionState.FAILED,
                        validation_notes=[f"Skill `{step.skill}` is missing from the registry."],
                        attempts=0,
                        error="missing_skill",
                    )
                )
                continue

            attempt = 0
            while True:
                attempt += 1
                payload = self._build_payload(
                    request=request,
                    intent=intent,
                    memory_context=memory_context,
                    prior_results=prior_results,
                )
                try:
                    output = self.runtime.execute(current_skill, payload, runtime_context)
                    validation = self.validator.validate_step(current_skill, output, step.expected_output, request)
                    if validation.status == CompletionState.FINISHED:
                        step_result = StepExecutionResult(
                            step_id=step.id,
                            skill=current_skill.name,
                            status=CompletionState.FINISHED,
                            output=output,
                            evidence=self._extract_evidence(output),
                            validation_status=validation.status,
                            validation_notes=validation.notes,
                            attempts=attempt,
                        )
                        step_results.append(step_result)
                        prior_results[current_skill.name] = output
                        artifacts[current_skill.name] = output
                        if self.memory and not safety.use_dry_run:
                            self.memory.remember_execution(current_skill.name, f"Completed objective: {intent.objective}")
                        break

                    recovery = self.recovery.for_validation(attempt, validation.status, step.fallback_skill)
                    if recovery.action == "retry":
                        continue
                    if recovery.action == "fallback" and recovery.fallback_skill:
                        fallback = self.registry.get(recovery.fallback_skill)
                        if fallback is not None:
                            current_skill = fallback
                            continue
                    step_results.append(
                        StepExecutionResult(
                            step_id=step.id,
                            skill=current_skill.name,
                            status=validation.status,
                            output=output,
                            evidence=self._extract_evidence(output),
                            validation_status=validation.status,
                            validation_notes=validation.notes + [recovery.reason],
                            attempts=attempt,
                        )
                    )
                    break
                except Exception as exc:
                    recovery = self.recovery.for_exception(attempt, exc, step.fallback_skill)
                    if recovery.action == "retry":
                        continue
                    if recovery.action == "fallback" and recovery.fallback_skill:
                        fallback = self.registry.get(recovery.fallback_skill)
                        if fallback is not None:
                            current_skill = fallback
                            continue
                    step_results.append(
                        StepExecutionResult(
                            step_id=step.id,
                            skill=current_skill.name,
                            status=CompletionState.FAILED,
                            output=None,
                            evidence=[],
                            validation_status=CompletionState.FAILED,
                            validation_notes=[recovery.reason],
                            attempts=attempt,
                            error=str(exc),
                        )
                    )
                    break

        final_status = self.validator.evaluate_plan(plan, step_results)
        result_text = self._summarize_artifacts(artifacts, step_results)
        risks = list(dict.fromkeys(safety.reasons + self._step_risks(step_results)))
        best_next_action = (
            "Review the dry-run output, then rerun without dry-run when approved."
            if safety.use_dry_run
            else self.composer.best_next_action(final_status)
        )
        return OperatorResult(
            objective=intent.objective,
            approach_taken=self._approach_lines(intent, routing, safety),
            result=result_text,
            validation_status=final_status,
            risks_or_limitations=risks,
            best_next_action=best_next_action,
            intent=intent,
            plan=plan,
            step_results=step_results,
            artifacts=artifacts,
        )

    def handle_as_text(self, request: str, confirmed: bool = False, dry_run: bool = False) -> str:
        return self.composer.compose(self.handle(request, confirmed=confirmed, dry_run=dry_run))

    def _build_payload(
        self,
        request: str,
        intent,
        memory_context: str,
        prior_results: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "request": request,
            "objective": intent.objective,
            "task_type": intent.task_type,
            "hidden_intent": intent.hidden_intent,
            "requested_output": intent.requested_output,
            "memory_context": memory_context,
            "prior_results": self._compact_prior_results(prior_results),
        }

    @staticmethod
    def _step_risks(step_results: list[StepExecutionResult]) -> list[str]:
        risks: list[str] = []
        for step in step_results:
            if step.status != CompletionState.FINISHED:
                risks.append(f"Step {step.step_id} ended with status `{step.status.value}`.")
            if step.error:
                risks.append(f"{step.skill}: {step.error}")
        return risks

    @staticmethod
    def _approach_lines(intent, routing, safety: SafetyDecision) -> list[str]:
        lines = [
            f"Intent resolved as `{intent.primary_intent.value}`.",
            f"Routing mode: `{routing.mode}`.",
        ]
        if routing.selected_skills:
            lines.append(f"Selected skills: {', '.join(routing.selected_skills)}.")
        if safety.use_dry_run:
            lines.append("Executed in dry-run mode.")
        return lines

    @staticmethod
    def _ordered_skill_names(skill_names: list[str]) -> list[str]:
        def priority(name: str) -> tuple[int, str]:
            lowered = name.lower()
            if "inspector" in lowered or "research" in lowered or "analyzer" in lowered:
                return (10, lowered)
            if "writer" in lowered or "publish" in lowered:
                return (90, lowered)
            return (50, lowered)

        return sorted(skill_names, key=priority)

    @staticmethod
    def _compact_prior_results(prior_results: dict[str, Any]) -> dict[str, Any]:
        compact: dict[str, Any] = {}
        for skill_name, result in prior_results.items():
            if not isinstance(result, dict):
                compact[skill_name] = result
                continue

            if "workspace_summary" in result:
                compact[skill_name] = {
                    "workspace_summary": result.get("workspace_summary"),
                    "key_files": result.get("key_files", [])[:12],
                }
            elif "brief_markdown" in result:
                compact[skill_name] = {"brief_markdown": result.get("brief_markdown")}
            elif "article_markdown" in result:
                compact[skill_name] = {"article_markdown": result.get("article_markdown")}
            elif "scorecard_markdown" in result:
                compact[skill_name] = {"scorecard_markdown": result.get("scorecard_markdown")}
            elif "content" in result:
                compact[skill_name] = {"content": result.get("content")}
            elif "summary" in result:
                compact[skill_name] = {
                    "summary": result.get("summary"),
                    "files_reviewed": result.get("files_reviewed", [])[:8],
                    "evidence": result.get("evidence", [])[:8],
                }
            elif "analysis_markdown" in result:
                compact[skill_name] = {
                    "analysis_markdown": result.get("analysis_markdown"),
                    "files_reviewed": result.get("files_reviewed", [])[:8],
                    "evidence": result.get("evidence", [])[:8],
                }
            elif "file_excerpt_markdown" in result:
                compact[skill_name] = {
                    "file_excerpt_markdown": result.get("file_excerpt_markdown"),
                    "files_reviewed": result.get("files_reviewed", [])[:8],
                    "evidence": result.get("evidence", [])[:8],
                }
            else:
                compact[skill_name] = {k: v for k, v in result.items() if k != "payload_preview"}
        return compact

    @staticmethod
    def _summarize_artifacts(artifacts: dict[str, Any], step_results: list[StepExecutionResult]) -> str:
        if artifacts:
            lines = []
            for key, value in artifacts.items():
                lines.append(f"[{key}]")
                if isinstance(value, dict):
                    if "summary" in value:
                        lines.append(str(value["summary"]))
                    elif "analysis_markdown" in value:
                        lines.append(str(value["analysis_markdown"]))
                    elif "file_excerpt_markdown" in value:
                        lines.append(str(value["file_excerpt_markdown"]))
                    elif "brief_markdown" in value:
                        lines.append(str(value["brief_markdown"]))
                    elif "article_markdown" in value:
                        lines.append(str(value["article_markdown"]))
                    elif "scorecard_markdown" in value:
                        lines.append(str(value["scorecard_markdown"]))
                    else:
                        lines.append(str({k: v for k, v in value.items() if k != "payload_preview"}))
                else:
                    lines.append(str(value))
            return "\n\n".join(lines)
        if step_results:
            return str(step_results[-1].output)
        return "No output produced."

    @staticmethod
    def _extract_evidence(output: Any) -> list[str]:
        if not isinstance(output, dict):
            return []

        evidence: list[str] = []
        if isinstance(output.get("evidence"), list):
            evidence.extend(str(item) for item in output["evidence"] if item)
        if isinstance(output.get("files_reviewed"), list):
            evidence.extend(f"file:{item}" for item in output["files_reviewed"] if item)
        if output.get("artifact_path"):
            evidence.append(f"artifact:{output['artifact_path']}")
        return list(dict.fromkeys(evidence))
