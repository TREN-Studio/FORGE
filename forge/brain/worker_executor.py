from __future__ import annotations

from pathlib import Path
from typing import Any

from forge.brain.contracts import AgentReview, CompletionState, ExecutionPlan, PlanStep, StepExecutionResult
from forge.brain.council import ActionAgent, CriticAgent, ResearchAgent
from forge.brain.worker_protocol import WorkerTask, WorkerTaskResult
from forge.config.settings import OperatorSettings
from forge.core.session import ForgeSession
from forge.skills.registry import SkillRegistry
from forge.skills.runtime import SkillExecutionContext, SkillRuntime
from forge.safety.sanitizer import PromptInjectionFirewall


def serialize_operator_settings(settings: OperatorSettings) -> dict[str, Any]:
    return {
        "workspace_root": str(settings.workspace_root),
        "enable_memory": settings.enable_memory,
        "shell_timeout_seconds": settings.shell_timeout_seconds,
        "shell_max_output_chars": settings.shell_max_output_chars,
        "prompt_injection_max_chars": settings.prompt_injection_max_chars,
        "browser_timeout_seconds": settings.browser_timeout_seconds,
        "browser_snapshot_limit": settings.browser_snapshot_limit,
        "browser_text_limit": settings.browser_text_limit,
        "browser_headless": settings.browser_headless,
        "artifact_dir_name": settings.artifact_dir_name,
    }


def deserialize_operator_settings(data: dict[str, Any]) -> OperatorSettings:
    return OperatorSettings(
        workspace_root=Path(str(data.get("workspace_root") or Path.cwd().resolve())).resolve(),
        enable_memory=bool(data.get("enable_memory", False)),
        shell_timeout_seconds=int(data.get("shell_timeout_seconds", 30)),
        shell_max_output_chars=int(data.get("shell_max_output_chars", 12000)),
        prompt_injection_max_chars=int(data.get("prompt_injection_max_chars", 6000)),
        browser_timeout_seconds=int(data.get("browser_timeout_seconds", 20)),
        browser_snapshot_limit=int(data.get("browser_snapshot_limit", 18)),
        browser_text_limit=int(data.get("browser_text_limit", 24)),
        browser_headless=bool(data.get("browser_headless", True)),
        artifact_dir_name=str(data.get("artifact_dir_name", ".forge_artifacts")),
    )


class WorkerTaskExecutor:
    """Serializable task executor used by both local lanes and external worker hosts."""

    def __init__(self) -> None:
        self._research = ResearchAgent()
        self._action = ActionAgent()
        self._critic = CriticAgent()
        self._runtime = SkillRuntime()
        self._session = ForgeSession(memory=False)
        self._registry_cache: dict[str, SkillRegistry] = {}

    def execute(self, task: WorkerTask, *, worker_id: str) -> WorkerTaskResult:
        output: Any
        if task.service_name == "council:action":
            output = self._execute_action(task)
        elif task.service_name == "council:research":
            output = self._execute_research(task)
        elif task.service_name == "council:critic":
            output = self._execute_critic(task)
        else:
            raise RuntimeError(f"Unsupported worker service: {task.service_name}")
        return WorkerTaskResult(
            task_id=task.task_id,
            service_name=task.service_name,
            operation=task.operation,
            status="completed",
            output=output,
            worker_id=worker_id,
        )

    def _execute_action(self, task: WorkerTask) -> Any:
        if task.operation == "dispatch_notes":
            step = PlanStep.model_validate(task.payload["step"])
            return self._action.dispatch_notes(step)
        if task.operation != "execute_skill":
            raise RuntimeError(f"Unsupported action operation: {task.operation}")

        settings = deserialize_operator_settings(task.payload["context"]["settings"])
        registry = self._registry_cache.get(str(settings.workspace_root))
        if registry is None:
            registry = SkillRegistry(settings)
            registry.refresh()
            self._registry_cache[str(settings.workspace_root)] = registry
        skill = registry.get(task.payload["skill_name"])
        if skill is None:
            raise RuntimeError(f"Skill not available on worker: {task.payload['skill_name']}")

        context = SkillExecutionContext(
            settings=settings,
            session=self._session,
            memory=None,
            dry_run=bool(task.payload["context"].get("dry_run", False)),
            sanitizer=PromptInjectionFirewall(max_chars=settings.prompt_injection_max_chars),
            state=dict(task.payload["context"].get("state", {})),
        )
        return self._runtime.execute(skill, dict(task.payload["skill_payload"]), context)

    def _execute_research(self, task: WorkerTask) -> Any:
        if task.operation == "prepare_step":
            step = PlanStep.model_validate(task.payload["step"])
            return self._research.prepare_step(task.payload["request"], step, dict(task.payload["payload"]))
        if task.operation == "enrich_output":
            step = PlanStep.model_validate(task.payload["step"])
            output, review = self._research.enrich_output(task.payload["request"], step, task.payload["output"])
            return {
                "output": output,
                "review": review.model_dump(mode="json") if review is not None else None,
            }
        raise RuntimeError(f"Unsupported research operation: {task.operation}")

    def _execute_critic(self, task: WorkerTask) -> Any:
        if task.operation == "review_step":
            step = PlanStep.model_validate(task.payload["step"])
            review = self._critic.review_step(
                task.payload["request"],
                step,
                task.payload["output"],
                CompletionState(task.payload["validation_status"]),
            )
            return review.model_dump(mode="json")
        if task.operation == "review_mission":
            plan = ExecutionPlan.model_validate(task.payload["plan"])
            step_results = [StepExecutionResult.model_validate(item) for item in task.payload["step_results"]]
            review = self._critic.review_mission(plan, step_results)
            return review.model_dump(mode="json")
        raise RuntimeError(f"Unsupported critic operation: {task.operation}")


def decode_agent_review(payload: Any) -> AgentReview:
    if isinstance(payload, AgentReview):
        return payload
    if not isinstance(payload, dict):
        raise TypeError("Agent review payload must be a dict.")
    return AgentReview.model_validate(payload)
