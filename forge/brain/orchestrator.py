from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Callable

from forge.brain.approval import ApprovalDecision, ApprovalPolicyEngine
from forge.brain.contracts import AgentReview, CompletionState, ExecutionPlan, PlanStep, StepExecutionResult, TaskIntent
from forge.brain.council import ActionAgent, CriticAgent, ResearchAgent
from forge.brain.mission_store import MissionAuditStore, MissionResumeState
from forge.brain.worker_executor import decode_agent_review, serialize_operator_settings
from forge.brain.worker_protocol import WorkerHeartbeat, WorkerRegistration, WorkerTask
from forge.brain.worker_runtime import DistributedCouncilRuntime
from forge.config.settings import OperatorSettings
from forge.recovery.manager import RecoveryManager
from forge.runtime.state_store import PersistentStateStore
from forge.skills.registry import SkillRegistry
from forge.skills.runtime import SkillExecutionContext, SkillRuntime
from forge.tools.workspace import WorkspaceTools
from forge.validation.validator import ResultValidator


@dataclass(slots=True)
class MissionExecution:
    step_results: list[StepExecutionResult]
    artifacts: dict[str, Any]
    mission_trace: list[str]
    mission_id: str
    audit_log_path: str
    resumed_from_step: str | None = None
    agent_reviews: list[AgentReview] = field(default_factory=list)


@dataclass(slots=True)
class _RollbackEntry:
    step_id: str
    skill: str
    data: dict[str, Any]


class MissionOrchestrator:
    """Dispatch mission steps with retries, checkpoints, audit logs, and a critic pass."""
    _shared_workers: DistributedCouncilRuntime | None = None
    _shared_approval_engine: ApprovalPolicyEngine | None = None

    @classmethod
    def worker_snapshot(cls) -> dict[str, Any]:
        if cls._shared_workers is None:
            cls._ensure_cluster()
        return cls._shared_workers.snapshot()

    @classmethod
    def register_worker(cls, registration: WorkerRegistration) -> None:
        if cls._shared_workers is None:
            cls._ensure_cluster()
        cls._shared_workers.register_remote_worker(registration)

    @classmethod
    def heartbeat_worker(cls, heartbeat: WorkerHeartbeat) -> None:
        if cls._shared_workers is None:
            cls._ensure_cluster()
        cls._shared_workers.heartbeat_worker(heartbeat)

    @classmethod
    def approvals_snapshot(cls) -> list[dict[str, Any]]:
        if cls._shared_approval_engine is None:
            cls._ensure_cluster()
        return cls._shared_approval_engine.list_pending()

    @classmethod
    def approval_status(cls, approval_id: str) -> dict[str, Any] | None:
        if cls._shared_approval_engine is None:
            cls._ensure_cluster()
        return cls._shared_approval_engine.approval_status(approval_id)

    @classmethod
    def approve(cls, approval_id: str, *, notes: str = "") -> dict[str, Any] | None:
        if cls._shared_approval_engine is None:
            cls._ensure_cluster()
        return cls._shared_approval_engine.approve(approval_id, notes=notes)

    @classmethod
    def reject(cls, approval_id: str, *, notes: str = "") -> dict[str, Any] | None:
        if cls._shared_approval_engine is None:
            cls._ensure_cluster()
        return cls._shared_approval_engine.reject(approval_id, notes=notes)

    @classmethod
    def _ensure_cluster(cls, state_store: PersistentStateStore | None = None, settings: OperatorSettings | None = None) -> None:
        active_settings = settings or OperatorSettings(workspace_root=Path.cwd().resolve())
        active_store = state_store or PersistentStateStore(
            active_settings.state_db_path,
            encryption_key_path=active_settings.approval_key_path,
        )
        if (
            cls._shared_workers is not None
            and cls._shared_approval_engine is not None
            and getattr(getattr(cls._shared_workers, "_state_store", None), "path", None) == active_store.path
        ):
            return
        if cls._shared_workers is not None:
            cls._shared_workers.close()
        cls._shared_workers = DistributedCouncilRuntime(
            state_store=active_store,
            max_queue_per_lane=active_settings.worker_max_queue_per_lane,
            remote_request_timeout_seconds=active_settings.worker_remote_timeout_seconds,
        )
        cls._shared_approval_engine = ApprovalPolicyEngine(active_store)

    def __init__(
        self,
        registry: SkillRegistry,
        runtime: SkillRuntime,
        validator: ResultValidator,
        recovery: RecoveryManager,
        audit_store: MissionAuditStore,
        *,
        compact_prior_results: Callable[[dict[str, Any]], dict[str, Any]],
        extract_evidence: Callable[[Any], list[str]],
    ) -> None:
        self._registry = registry
        self._runtime = runtime
        self._validator = validator
        self._recovery = recovery
        self._audit_store = audit_store
        self._compact_prior_results = compact_prior_results
        self._extract_evidence = extract_evidence
        self._research_agent = ResearchAgent()
        self._action_agent = ActionAgent()
        self._critic_agent = CriticAgent()
        MissionOrchestrator._ensure_cluster(audit_store.state_store, registry._settings if hasattr(registry, "_settings") else None)
        self._workers = MissionOrchestrator._shared_workers
        self._approval_engine = MissionOrchestrator._shared_approval_engine

    def execute(
        self,
        request: str,
        intent: TaskIntent,
        plan: ExecutionPlan,
        runtime_context: SkillExecutionContext,
        *,
        mission_id: str,
        audit_log_path: str,
        resume_state: MissionResumeState | None = None,
        confirmed: bool = False,
        memory_context: str = "",
        remember_execution: Callable[[str, str], None] | None = None,
    ) -> MissionExecution:
        step_results = list(resume_state.completed_steps) if resume_state else []
        artifacts = self._resume_artifacts(resume_state)
        mission_trace = list(resume_state.mission_trace) if resume_state else []
        if mission_trace:
            mission_trace.append(f"Mission resumed for objective: {intent.objective}")
        else:
            mission_trace = [f"Mission started for objective: {intent.objective}"]
        completed_step_ids = self._resumable_step_ids(plan, resume_state, step_results)
        if resume_state is not None:
            step_results = [step for step in step_results if step.step_id in completed_step_ids]
            artifacts = {key: value for key, value in artifacts.items() if key in completed_step_ids}
        prior_results = self._prior_results_from_steps(step_results)
        resumed_from_step = step_results[-1].step_id if step_results else None
        rollback_stack = self._rebuild_rollback_stack(step_results)
        critique_memory = self._restore_critique_memory(mission_id, step_results)
        agent_reviews: list[AgentReview] = []
        mission_failed = False
        mission_status = "running"

        self._persist_progress(
            mission_id,
            audit_log_path,
            request,
            plan,
            mission_status,
            step_results,
            artifacts,
            mission_trace,
            resumed_from_step,
        )

        for step in plan.steps:
            if step.id in completed_step_ids:
                mission_trace.append(f"{step.id}: skipped because it already completed in mission {mission_id}.")
                continue

            mission_trace.append(f"{step.id}: dispatch {step.tool or step.skill or 'reasoning'}")

            if step.skill is None:
                result, review = self._execute_reasoning_step(request, intent, step, runtime_context)
                step_results.append(result)
                agent_reviews.append(review)
                artifacts[step.id] = {"summary": str(result.output)}
                mission_status = result.status.value
                self._persist_progress(
                    mission_id,
                    audit_log_path,
                    request,
                    plan,
                    mission_status,
                    step_results,
                    artifacts,
                    mission_trace,
                    resumed_from_step,
                )
                continue

            current_skill = self._registry.get(step.skill)
            if current_skill is None:
                step_results.append(
                    StepExecutionResult(
                        step_id=step.id,
                        skill=step.skill,
                        tool=step.tool or step.skill,
                        status=CompletionState.FAILED,
                        output=None,
                        validation_status=CompletionState.FAILED,
                        validation_notes=[f"Skill `{step.skill}` is missing from the registry."],
                        attempts=0,
                        input_snapshot=step.input_spec,
                        trace=["Dispatch failed before execution."],
                        error="missing_skill",
                    )
                )
                mission_trace.append(f"{step.id}: missing skill `{step.skill}`.")
                mission_status = CompletionState.FAILED.value
                self._persist_progress(
                    mission_id,
                    audit_log_path,
                    request,
                    plan,
                    mission_status,
                    step_results,
                    artifacts,
                    mission_trace,
                    resumed_from_step,
                )
                if step.stop_on_failure:
                    mission_failed = True
                    break
                continue

            attempt = 0
            last_output: Any = None
            last_validation_notes: list[str] = []
            rolled_back = False
            rollback_notes: list[str] = []
            step_trace: list[str] = []
            step_completed = False

            while attempt < max(1, step.retry_limit):
                attempt += 1
                payload = self._build_step_payload(
                    request=request,
                    intent=intent,
                    memory_context=memory_context,
                    prior_results=prior_results,
                    critique_memory=critique_memory,
                    step=step,
                )
                step_trace.extend(self._workers.submit_task(self._dispatch_notes_task(step, mission_id=mission_id, attempt=attempt)))
                step_trace.extend(
                    self._workers.submit_task(
                        self._prepare_research_task(
                            request=request,
                            step=step,
                            payload=payload,
                            mission_id=mission_id,
                            attempt=attempt,
                        )
                    )
                )
                if critique_memory:
                    step_trace.append(f"Cross-agent critique injected: {sum(len(v) for v in critique_memory.values())} note(s).")

                input_snapshot = self._input_snapshot(payload)
                approval_decision = self._approval_decision(
                    mission_id=mission_id,
                    request=request,
                    step=step,
                    payload=payload,
                    confirmed=confirmed,
                )
                if approval_decision.approval_required:
                    checkpoint_review = AgentReview(
                        agent="critic",
                        status=CompletionState.NEEDS_HUMAN_CONFIRMATION,
                        notes=approval_decision.notes,
                        confidence=0.2,
                    )
                    agent_reviews.append(checkpoint_review)
                    self._remember_critique(mission_id, critique_memory, step.id, current_skill.name, checkpoint_review)
                    step_trace.append("Approval checkpoint blocked execution before side effects.")
                    step_trace.extend(f"Checkpoint: {note}" for note in approval_decision.notes)
                    artifacts.setdefault("pending_approvals", {})[step.id] = {
                        "approval_id": approval_decision.approval_id,
                        "approval_class": approval_decision.approval_class,
                        "status": approval_decision.status,
                    }
                    step_results.append(
                        StepExecutionResult(
                            step_id=step.id,
                            skill=current_skill.name,
                            tool=step.tool or current_skill.name,
                            status=CompletionState.NEEDS_HUMAN_CONFIRMATION,
                            output=None,
                            evidence=[],
                            validation_status=CompletionState.NEEDS_HUMAN_CONFIRMATION,
                            validation_notes=approval_decision.notes,
                            attempts=attempt,
                            input_snapshot=input_snapshot,
                            trace=list(step_trace),
                            agent_reviews=self._review_lines([checkpoint_review]),
                        )
                    )
                    mission_trace.append(f"{step.id}: waiting for explicit approval.")
                    mission_status = CompletionState.NEEDS_HUMAN_CONFIRMATION.value
                    mission_failed = True
                    self._persist_progress(
                        mission_id,
                        audit_log_path,
                        request,
                        plan,
                        mission_status,
                        step_results,
                        artifacts,
                        mission_trace,
                        resumed_from_step,
                    )
                    break

                step_trace.append(f"Attempt {attempt}: dispatching `{current_skill.name}`.")
                step_trace.append(f"Input snapshot: {input_snapshot}")

                try:
                    if current_skill.name == "browser-executor" and isinstance(payload.get("fanout_targets"), list) and len(payload["fanout_targets"]) > 1:
                        step_trace.append(f"Fan-out dispatch across {len(payload['fanout_targets'])} browser target(s).")
                        output = self._execute_browser_fanout(current_skill, payload, runtime_context, mission_id=mission_id, step_id=step.id, attempt=attempt)
                    else:
                        step_trace.append("Worker lane: council:action")
                        output = self._workers.submit_task(
                            self._execute_skill_task(
                                current_skill.name,
                                payload,
                                runtime_context,
                                mission_id=mission_id,
                                step_id=step.id,
                                attempt=attempt,
                            )
                        )
                    last_output = output

                    research_review: AgentReview | None = None
                    step_trace.append("Worker lane: council:research")
                    research_result = self._workers.submit_task(
                        self._enrich_research_task(
                            request=request,
                            step=step,
                            output=output,
                            mission_id=mission_id,
                            attempt=attempt,
                        )
                    )
                    output = research_result.get("output")
                    review_payload = research_result.get("review")
                    research_review = decode_agent_review(review_payload) if review_payload else None
                    if research_review is not None:
                        agent_reviews.append(research_review)
                        step_trace.extend(f"Research: {note}" for note in research_review.notes)

                    validation = self._validator.validate_step(
                        current_skill,
                        output,
                        step.expected_output,
                        request,
                        workspace_root=runtime_context.settings.workspace_root,
                    )
                    last_validation_notes = validation.notes
                    critique = self._critique_step(step, output, validation.status)
                    combined_notes = list(last_validation_notes)
                    if critique:
                        combined_notes = list(dict.fromkeys(combined_notes + critique))
                    step_trace.append("Worker lane: council:critic")
                    critic_review = decode_agent_review(
                        self._workers.submit_task(
                            self._critic_review_task(
                                request=request,
                                step=step,
                                output=output,
                                validation_status=validation.status,
                                mission_id=mission_id,
                                attempt=attempt,
                            )
                        )
                    )
                    agent_reviews.append(critic_review)
                    self._remember_critique(mission_id, critique_memory, step.id, current_skill.name, critic_review)
                    final_status = self._merge_status(validation.status, critic_review.status)
                    combined_notes = list(dict.fromkeys(combined_notes + critic_review.notes))

                    step_trace.append(f"Validation result: {validation.status.value}.")
                    if critique:
                        step_trace.extend(f"Critique: {note}" for note in critique)
                    step_trace.extend(f"Critic: {note}" for note in critic_review.notes)

                    if final_status == CompletionState.FINISHED:
                        step_result = StepExecutionResult(
                            step_id=step.id,
                            skill=current_skill.name,
                            tool=step.tool or current_skill.name,
                            status=CompletionState.FINISHED,
                            output=output,
                            evidence=self._extract_evidence(output),
                            validation_status=validation.status,
                            validation_notes=combined_notes,
                            attempts=attempt,
                            input_snapshot=input_snapshot,
                            trace=list(step_trace),
                            agent_reviews=self._review_lines([review for review in (research_review, critic_review) if review is not None]),
                        )
                        step_results.append(step_result)
                        prior_results[current_skill.name] = output
                        artifacts[step.id] = output

                        rollback_entry = self._capture_rollback(step.id, current_skill.name, output)
                        if rollback_entry is not None:
                            rollback_stack.append(rollback_entry)
                            step_trace.append("Rollback checkpoint captured.")

                        if remember_execution and not runtime_context.dry_run:
                            remember_execution(current_skill.name, f"Completed sub-task: {step.action}")
                        mission_trace.append(f"{step.id}: completed via `{current_skill.name}` on attempt {attempt}.")
                        mission_status = CompletionState.FINISHED.value
                        self._persist_progress(
                            mission_id,
                            audit_log_path,
                            request,
                            plan,
                            mission_status,
                            step_results,
                            artifacts,
                            mission_trace,
                            resumed_from_step,
                        )
                        step_completed = True
                        break

                    if step.rollback_on_failure:
                        rolled_back, rollback_notes = self._rollback_step_output(output, runtime_context)
                        if rolled_back:
                            step_trace.extend(f"Rollback: {note}" for note in rollback_notes)

                    recovery = self._recovery.for_validation(attempt, final_status, step.fallback_skill)
                    if recovery.action == "retry" and attempt < max(1, step.retry_limit):
                        step_trace.append(f"Retrying step: {recovery.reason}")
                        mission_trace.append(f"{step.id}: retry scheduled after critic/validation review.")
                        continue
                    if recovery.action == "fallback" and recovery.fallback_skill:
                        fallback = self._registry.get(recovery.fallback_skill)
                        if fallback is not None:
                            step_trace.append(f"Switching to fallback skill `{fallback.name}`.")
                            current_skill = fallback
                            continue

                    step_results.append(
                        StepExecutionResult(
                            step_id=step.id,
                            skill=current_skill.name,
                            tool=step.tool or current_skill.name,
                            status=final_status,
                            output=output,
                            evidence=self._extract_evidence(output),
                            validation_status=validation.status,
                            validation_notes=list(dict.fromkeys(combined_notes + [recovery.reason])),
                            attempts=attempt,
                            input_snapshot=input_snapshot,
                            trace=list(step_trace),
                            rolled_back=rolled_back,
                            rollback_notes=rollback_notes,
                            agent_reviews=self._review_lines([review for review in (research_review, critic_review) if review is not None]),
                        )
                    )
                    artifacts[step.id] = output
                    mission_trace.append(f"{step.id}: {final_status.value} after {attempt} attempt(s).")
                    mission_status = final_status.value
                    self._persist_progress(
                        mission_id,
                        audit_log_path,
                        request,
                        plan,
                        mission_status,
                        step_results,
                        artifacts,
                        mission_trace,
                        resumed_from_step,
                    )
                    if step.stop_on_failure:
                        mission_failed = True
                    break
                except Exception as exc:
                    recovery = self._recovery.for_exception(attempt, exc, step.fallback_skill)
                    step_trace.append(f"Exception: {exc}")
                    step_trace.append("Worker lane: council:critic")
                    critic_review = self._workers.submit(
                        "council:critic",
                        lambda exc=exc: AgentReview(
                            agent="critic",
                            status=CompletionState.FAILED,
                            notes=[f"Critic observed execution failure: {exc}."],
                            confidence=0.1,
                        ),
                    )
                    agent_reviews.append(critic_review)
                    self._remember_critique(mission_id, critique_memory, step.id, current_skill.name, critic_review)
                    if self._is_missing_file_editor_content_failure(current_skill.name, exc) and attempt < max(1, step.retry_limit):
                        recovery_result = self._execute_content_recovery_step(
                            request=request,
                            intent=intent,
                            memory_context=memory_context,
                            critique_memory=critique_memory,
                            failed_step=step,
                            failed_payload=payload,
                            runtime_context=runtime_context,
                            mission_id=mission_id,
                            attempt=attempt,
                        )
                        if recovery_result is not None and recovery_result.status == CompletionState.FINISHED:
                            step_results.append(recovery_result)
                            if recovery_result.skill:
                                prior_results[recovery_result.skill] = recovery_result.output
                            artifacts[recovery_result.step_id] = recovery_result.output
                            mission_trace.append(f"{step.id}: recovered missing editor content via `{recovery_result.skill}`.")
                            step_trace.append("Recovery inserted evidence step, then retrying file-editor with preserved evidence.")
                            self._persist_progress(
                                mission_id,
                                audit_log_path,
                                request,
                                plan,
                                CompletionState.NEEDS_RETRY.value,
                                step_results,
                                artifacts,
                                mission_trace,
                                resumed_from_step,
                            )
                            continue
                    if recovery.action == "retry" and attempt < max(1, step.retry_limit):
                        step_trace.append(f"Retrying step: {recovery.reason}")
                        mission_trace.append(f"{step.id}: retrying after exception `{exc}`.")
                        continue
                    if recovery.action == "fallback" and recovery.fallback_skill:
                        fallback = self._registry.get(recovery.fallback_skill)
                        if fallback is not None:
                            step_trace.append(f"Switching to fallback skill `{fallback.name}`.")
                            current_skill = fallback
                            continue

                    step_results.append(
                        StepExecutionResult(
                            step_id=step.id,
                            skill=current_skill.name,
                            tool=step.tool or current_skill.name,
                            status=CompletionState.FAILED,
                            output=last_output,
                            evidence=self._extract_evidence(last_output) if last_output is not None else [],
                            validation_status=CompletionState.FAILED,
                            validation_notes=list(dict.fromkeys(last_validation_notes + [recovery.reason] + critic_review.notes)),
                            attempts=attempt,
                            input_snapshot=input_snapshot,
                            trace=list(step_trace),
                            rolled_back=rolled_back,
                            rollback_notes=rollback_notes,
                            agent_reviews=self._review_lines([critic_review]),
                            error=str(exc),
                        )
                    )
                    mission_trace.append(f"{step.id}: failed due to `{exc}`.")
                    mission_status = CompletionState.FAILED.value
                    self._persist_progress(
                        mission_id,
                        audit_log_path,
                        request,
                        plan,
                        mission_status,
                        step_results,
                        artifacts,
                        mission_trace,
                        resumed_from_step,
                    )
                    if step.stop_on_failure:
                        mission_failed = True
                    break

            if not step_completed and mission_failed:
                mission_trace.append(f"{step.id}: mission halted because this step is marked stop_on_failure.")
                break

        if mission_failed and rollback_stack and not runtime_context.dry_run:
            mission_trace.append("Mission aborted. Rolling back completed mutable steps.")
            rollback_events = self._rollback_stack(rollback_stack, runtime_context, step_results)
            mission_trace.extend(rollback_events)

        mission_review = decode_agent_review(
            self._workers.submit_task(
                self._critic_mission_review_task(
                    plan=plan,
                    step_results=step_results,
                    mission_id=mission_id,
                )
            )
        )
        agent_reviews.append(mission_review)
        mission_trace.extend(f"Critic review: {note}" for note in mission_review.notes)

        artifacts["mission_trace"] = {"trace_markdown": self._format_trace_markdown(mission_trace, step_results)}
        artifacts["mission_audit"] = {
            "mission_id": mission_id,
            "audit_log_path": audit_log_path,
            "resumed_from_step": resumed_from_step,
        }
        artifacts["agent_reviews"] = [review.model_dump(mode="json") for review in agent_reviews]
        artifacts["worker_lanes"] = {"lanes": self._workers.snapshot()}

        final_status = self._final_mission_status(step_results, mission_review.status)
        self._persist_progress(
            mission_id,
            audit_log_path,
            request,
            plan,
            final_status.value,
            step_results,
            artifacts,
            mission_trace,
            resumed_from_step,
        )
        return MissionExecution(
            step_results=step_results,
            artifacts=artifacts,
            mission_trace=mission_trace,
            mission_id=mission_id,
            audit_log_path=audit_log_path,
            resumed_from_step=resumed_from_step,
            agent_reviews=agent_reviews,
        )

    def _execute_reasoning_step(
        self,
        request: str,
        intent: TaskIntent,
        step,
        runtime_context: SkillExecutionContext,
    ) -> tuple[StepExecutionResult, AgentReview]:
        reasoning_output = runtime_context.session.ask(request, task_type=intent.task_type, remember=False)
        validation = self._validator.validate_step(
            None,
            reasoning_output,
            step.expected_output,
            request,
            workspace_root=runtime_context.settings.workspace_root,
        )
        critic_review = self._critic_agent.review_step(request, step, {"content": reasoning_output}, validation.status)
        step_trace = [
            "Reasoning-only step dispatched.",
            f"Validation result: {validation.status.value}.",
            *[f"Critic: {note}" for note in critic_review.notes],
        ]
        return (
            StepExecutionResult(
                step_id=step.id,
                skill=None,
                tool=step.tool,
                status=critic_review.status,
                output=reasoning_output,
                evidence=[],
                validation_status=validation.status,
                validation_notes=list(dict.fromkeys(validation.notes + critic_review.notes)),
                attempts=1,
                input_snapshot={"mode": "reasoning_only"},
                trace=step_trace,
                agent_reviews=self._review_lines([critic_review]),
            ),
            critic_review,
        )

    def _persist_progress(
        self,
        mission_id: str,
        audit_log_path: str,
        request: str,
        plan: ExecutionPlan,
        status: str,
        step_results: list[StepExecutionResult],
        artifacts: dict[str, Any],
        mission_trace: list[str],
        resumed_from_step: str | None,
    ) -> None:
        self._audit_store.save_progress(
            mission_id,
            audit_log_path,
            request=request,
            plan=plan,
            status=status,
            step_results=step_results,
            artifacts=artifacts,
            mission_trace=mission_trace,
            resumed_from_step=resumed_from_step,
        )

    def _build_step_payload(
        self,
        *,
        request: str,
        intent: TaskIntent,
        memory_context: str,
        prior_results: dict[str, Any],
        critique_memory: dict[str, list[str]],
        step,
    ) -> dict[str, Any]:
        payload = {
            "request": request,
            "objective": intent.objective,
            "task_type": intent.task_type,
            "hidden_intent": intent.hidden_intent,
            "requested_output": intent.requested_output,
            "memory_context": memory_context,
            "prior_results": self._compact_prior_results(prior_results),
            "prior_critiques": {key: value[:6] for key, value in critique_memory.items()},
            "step_id": step.id,
            "step_action": step.action,
            "step_tool": step.tool or step.skill,
            "depends_on": step.depends_on,
        }
        payload.update(step.input_spec)
        if step.skill == "file-editor":
            self._inject_file_editor_content(payload, prior_results)
        return payload

    def _dispatch_notes_task(self, step, *, mission_id: str, attempt: int) -> WorkerTask:
        return WorkerTask(
            service_name="council:action",
            operation="dispatch_notes",
            payload={"step": step.model_dump(mode="json")},
            mission_id=mission_id,
            step_id=step.id,
            idempotency_key=self._idempotency_key(mission_id, step.id, "dispatch_notes", attempt),
            remote_allowed=True,
            lease_ttl_seconds=self._audit_store._settings.worker_lease_ttl_seconds,
        )

    def _prepare_research_task(
        self,
        *,
        request: str,
        step,
        payload: dict[str, Any],
        mission_id: str,
        attempt: int,
    ) -> WorkerTask:
        return WorkerTask(
            service_name="council:research",
            operation="prepare_step",
            payload={
                "request": request,
                "step": step.model_dump(mode="json"),
                "payload": payload,
            },
            mission_id=mission_id,
            step_id=step.id,
            idempotency_key=self._idempotency_key(mission_id, step.id, "prepare_step", attempt, self._payload_fingerprint(payload)),
            remote_allowed=True,
            lease_ttl_seconds=self._audit_store._settings.worker_lease_ttl_seconds,
        )

    def _execute_skill_task(
        self,
        skill_name: str,
        payload: dict[str, Any],
        runtime_context: SkillExecutionContext,
        *,
        mission_id: str,
        step_id: str,
        attempt: int,
    ) -> WorkerTask:
        return WorkerTask(
            service_name="council:action",
            operation="execute_skill",
            payload={
                "skill_name": skill_name,
                "skill_payload": payload,
                "context": {
                    "settings": serialize_operator_settings(runtime_context.settings),
                    "dry_run": runtime_context.dry_run,
                    "state": dict(runtime_context.state),
                },
            },
            mission_id=mission_id,
            step_id=step_id,
            idempotency_key=self._idempotency_key(
                mission_id,
                step_id,
                f"execute:{skill_name}",
                attempt,
                self._payload_fingerprint(payload),
            ),
            remote_allowed=True,
            lease_ttl_seconds=runtime_context.settings.worker_lease_ttl_seconds,
            timeout_seconds=runtime_context.settings.worker_remote_timeout_seconds,
        )

    def _enrich_research_task(
        self,
        *,
        request: str,
        step,
        output: Any,
        mission_id: str,
        attempt: int,
    ) -> WorkerTask:
        return WorkerTask(
            service_name="council:research",
            operation="enrich_output",
            payload={
                "request": request,
                "step": step.model_dump(mode="json"),
                "output": output,
            },
            mission_id=mission_id,
            step_id=step.id,
            idempotency_key=self._idempotency_key(mission_id, step.id, "enrich_output", attempt, self._payload_fingerprint(output)),
            remote_allowed=True,
            lease_ttl_seconds=self._audit_store._settings.worker_lease_ttl_seconds,
        )

    def _critic_review_task(
        self,
        *,
        request: str,
        step,
        output: Any,
        validation_status: CompletionState,
        mission_id: str,
        attempt: int,
    ) -> WorkerTask:
        return WorkerTask(
            service_name="council:critic",
            operation="review_step",
            payload={
                "request": request,
                "step": step.model_dump(mode="json"),
                "output": output,
                "validation_status": validation_status.value,
            },
            mission_id=mission_id,
            step_id=step.id,
            idempotency_key=self._idempotency_key(
                mission_id,
                step.id,
                "critic_review",
                attempt,
                self._payload_fingerprint({"output": output, "status": validation_status.value}),
            ),
            remote_allowed=True,
            lease_ttl_seconds=self._audit_store._settings.worker_lease_ttl_seconds,
        )

    def _critic_mission_review_task(
        self,
        *,
        plan: ExecutionPlan,
        step_results: list[StepExecutionResult],
        mission_id: str,
    ) -> WorkerTask:
        return WorkerTask(
            service_name="council:critic",
            operation="review_mission",
            payload={
                "plan": plan.model_dump(mode="json"),
                "step_results": [step.model_dump(mode="json") for step in step_results],
            },
            mission_id=mission_id,
            step_id="mission",
            idempotency_key=self._idempotency_key(
                mission_id,
                "mission",
                "critic_mission",
                len(step_results),
                self._payload_fingerprint([step.model_dump(mode="json") for step in step_results]),
            ),
            remote_allowed=True,
            lease_ttl_seconds=self._audit_store._settings.worker_lease_ttl_seconds,
        )

    def _approval_decision(
        self,
        *,
        mission_id: str,
        request: str,
        step,
        payload: dict[str, Any],
        confirmed: bool,
    ) -> ApprovalDecision:
        approval_class = self._approval_class_for_step(step, payload, request)
        return self._approval_engine.evaluate(
            mission_id=mission_id,
            step_id=step.id,
            approval_class=approval_class,
            request_excerpt=request,
            payload=payload,
            summary=step.action,
            confirmed=confirmed,
        )

    @staticmethod
    def _approval_class_for_step(step, payload: dict[str, Any], request: str) -> str | None:
        request_lower = request.lower()
        if step.skill == "file-editor":
            target = str(payload.get("target_path", "")).lower()
            if any(token in target for token in (".env", "secret", "credential", "token", "password", "key")):
                return "sensitive_file_write"
        if step.skill == "shell-executor":
            command = str(payload.get("command", "")).lower()
            if any(token in command for token in ("post ", "-x post", "--request post")):
                return "network_post"
            if any(token in command for token in ("curl", "wget", "invoke-webrequest", "http://", "https://")):
                return "network_egress"
            if any(token in command for token in ("rm ", "del ", "remove-item", "shutdown", "format", "drop ")):
                return "destructive_shell"
        if step.skill == "browser-executor":
            if any(token in request_lower for token in ("login", "signin", "password", "checkout", "purchase", "buy", "pay", "account")):
                return "authenticated_browser"
            if any(token in request_lower for token in ("publish", "post live", "deploy", "submit form", "upload")):
                return "external_publish"
        if step.skill == "github-publisher":
            return "network_post"
        if step.skill in {"wordpress-publisher", "external-publisher"}:
            return "external_publish"
        return None

    @staticmethod
    def _idempotency_key(mission_id: str, step_id: str, operation: str, attempt: int, extra: str = "") -> str:
        return f"{mission_id}:{step_id}:{operation}:{attempt}:{extra}".strip(":")

    @staticmethod
    def _payload_fingerprint(payload: Any) -> str:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha1(encoded.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _resume_artifacts(resume_state: MissionResumeState | None) -> dict[str, Any]:
        if resume_state is None:
            return {}
        artifacts = dict(resume_state.artifacts)
        artifacts.pop("mission_trace", None)
        artifacts.pop("agent_reviews", None)
        artifacts.pop("worker_lanes", None)
        return artifacts

    @staticmethod
    def _prior_results_from_steps(step_results: list[StepExecutionResult]) -> dict[str, Any]:
        prior_results: dict[str, Any] = {}
        for step in step_results:
            if step.skill and step.status == CompletionState.FINISHED and not step.rolled_back:
                prior_results[step.skill] = step.output
        return prior_results

    def _rebuild_rollback_stack(self, step_results: list[StepExecutionResult]) -> list[_RollbackEntry]:
        stack: list[_RollbackEntry] = []
        for step in step_results:
            if step.rolled_back or step.status != CompletionState.FINISHED or not step.skill:
                continue
            rollback_entry = self._capture_rollback(step.step_id, step.skill, step.output)
            if rollback_entry is not None:
                stack.append(rollback_entry)
        return stack

    @staticmethod
    def _input_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "step_id": payload.get("step_id"),
            "step_tool": payload.get("step_tool"),
            "requested_output": payload.get("requested_output"),
            "input_spec_keys": sorted(
                key
                for key in payload.keys()
                if key
                not in {
                    "request",
                    "objective",
                    "task_type",
                    "hidden_intent",
                    "memory_context",
                    "prior_results",
                    "prior_critiques",
                    "step_id",
                    "step_action",
                    "step_tool",
                    "depends_on",
                }
            ),
            "prior_result_keys": sorted(payload.get("prior_results", {}).keys())[:10],
        }

    @staticmethod
    def _critique_step(step, output: Any, status: CompletionState) -> list[str]:
        notes: list[str] = []
        if step.skill == "browser-executor" and status == CompletionState.FINISHED:
            action_count = len(output.get("action_results", [])) if isinstance(output, dict) else 0
            notes.append(f"Action trace captured for {action_count} browser action(s).")
        if step.skill == "file-editor" and isinstance(output, dict) and output.get("changed") is False:
            notes.append("File editor reported no textual change.")
        if step.skill == "shell-executor" and isinstance(output, dict) and not output.get("stdout") and not output.get("stderr"):
            notes.append("Shell command produced minimal output.")
        return notes

    @staticmethod
    def _capture_rollback(step_id: str, skill_name: str, output: Any) -> _RollbackEntry | None:
        if skill_name != "file-editor" or not isinstance(output, dict):
            return None
        rollback = output.get("rollback")
        if not isinstance(rollback, dict):
            return None
        return _RollbackEntry(step_id=step_id, skill=skill_name, data=rollback)

    @staticmethod
    def _rollback_step_output(output: Any, runtime_context: SkillExecutionContext) -> tuple[bool, list[str]]:
        if not isinstance(output, dict):
            return False, []
        rollback = output.get("rollback")
        if not isinstance(rollback, dict):
            return False, []
        tools = WorkspaceTools(runtime_context.settings)
        result = tools.rollback_text_edit(
            rollback["path"],
            existed_before=bool(rollback.get("existed_before")),
            previous_content=str(rollback.get("previous_content", "")),
        )
        note = f"Reverted `{result['path']}`."
        if result.get("deleted"):
            note = f"Deleted newly created file `{result['path']}` during rollback."
        return True, [note]

    def _resumable_step_ids(
        self,
        plan: ExecutionPlan,
        resume_state: MissionResumeState | None,
        step_results: list[StepExecutionResult],
    ) -> set[str]:
        if resume_state is None:
            return {step.step_id for step in step_results if step.status == CompletionState.FINISHED and not step.rolled_back}

        saved_steps = {
            step.get("id"): step
            for step in (resume_state.saved_plan.get("steps") or [])
            if isinstance(step, dict) and step.get("id")
        }
        resumable: set[str] = set()
        for step in plan.steps:
            matched_result = next(
                (
                    result
                    for result in step_results
                    if result.step_id == step.id and result.status == CompletionState.FINISHED and not result.rolled_back
                ),
                None,
            )
            if matched_result is None:
                break
            saved_step = saved_steps.get(step.id)
            if saved_step is None or self._step_signature_from_saved(saved_step) != self._step_signature(step):
                break
            resumable.add(step.id)
        return resumable

    @staticmethod
    def _step_signature(step) -> str:
        return f"{step.id}|{step.skill}|{step.tool}|{step.action}|{step.input_spec}"

    @staticmethod
    def _step_signature_from_saved(step: dict[str, Any]) -> str:
        return f"{step.get('id')}|{step.get('skill')}|{step.get('tool')}|{step.get('action')}|{step.get('input_spec')}"

    def _rollback_stack(
        self,
        rollback_stack: list[_RollbackEntry],
        runtime_context: SkillExecutionContext,
        step_results: list[StepExecutionResult],
    ) -> list[str]:
        tools = WorkspaceTools(runtime_context.settings)
        notes: list[str] = []
        for entry in reversed(rollback_stack):
            result = tools.rollback_text_edit(
                entry.data["path"],
                existed_before=bool(entry.data.get("existed_before")),
                previous_content=str(entry.data.get("previous_content", "")),
            )
            note = f"Rollback executed for {entry.step_id} -> `{result['path']}`."
            if result.get("deleted"):
                note = f"Rollback executed for {entry.step_id} -> deleted `{result['path']}`."
            notes.append(note)
            for step_result in step_results:
                if step_result.step_id == entry.step_id:
                    step_result.rolled_back = True
                    step_result.rollback_notes.append(note)
        return notes

    def _execute_browser_fanout(
        self,
        current_skill,
        payload: dict[str, Any],
        runtime_context: SkillExecutionContext,
        *,
        mission_id: str,
        step_id: str,
        attempt: int,
    ) -> dict[str, Any]:
        targets = [str(item).strip() for item in payload.get("fanout_targets", []) if str(item).strip()]
        if len(targets) <= 1:
            return self._runtime.execute(current_skill, payload, runtime_context)

        futures = []
        for index, target in enumerate(targets, start=1):
            child_payload = dict(payload)
            child_payload["start_url"] = target
            child_payload.pop("fanout_targets", None)
            futures.append(
                self._workers.submit_task_future(
                    self._execute_skill_task(
                        current_skill.name,
                        child_payload,
                        runtime_context,
                        mission_id=mission_id,
                        step_id=f"{step_id}:fanout:{index}",
                        attempt=attempt,
                    )
                )
            )

        results = [future.result() for future in futures]
        merged_page_state = self._merge_browser_page_state(results)
        combined_snapshot = "\n\n".join(
            f"## Source {index + 1}\n{result.get('snapshot_text', '').strip()}"
            for index, result in enumerate(results)
            if str(result.get("snapshot_text", "")).strip()
        ).strip()
        visited_urls = [str(result.get("current_url", "")).strip() for result in results if str(result.get("current_url", "")).strip()]
        action_trace = "\n\n".join(
            f"## Source {index + 1}\n{result.get('action_trace', '').strip()}"
            for index, result in enumerate(results)
            if str(result.get("action_trace", "")).strip()
        ).strip()
        action_results: list[dict[str, Any]] = []
        for index, result in enumerate(results, start=1):
            for child_action in result.get("action_results", []):
                if not isinstance(child_action, dict):
                    continue
                merged_action = dict(child_action)
                merged_action.setdefault("source_index", index)
                merged_action.setdefault("source_url", result.get("current_url", ""))
                action_results.append(merged_action)
        evidence: list[str] = []
        for result in results:
            evidence.extend(str(item) for item in result.get("evidence", []) if item)
        evidence.append(f"fanout_sources:{len(results)}")
        return {
            "status": "completed",
            "summary": f"Browser fan-out completed across {len(results)} source(s).",
            "title": f"Browser fan-out across {len(results)} source(s)",
            "current_url": visited_urls[0] if visited_urls else "",
            "visited_urls": visited_urls,
            "action_results": action_results,
            "fanout_results": results,
            "page_state": merged_page_state,
            "snapshot_text": combined_snapshot,
            "action_trace": action_trace,
            "source_count": len(results),
            "evidence": list(dict.fromkeys(evidence)),
        }

    @staticmethod
    def _merge_browser_page_state(results: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        merged: dict[str, list[dict[str, Any]]] = {
            "headings": [],
            "buttons": [],
            "inputs": [],
            "links": [],
            "text": [],
        }
        seen: set[tuple[str, str, str]] = set()
        for result in results:
            page_state = result.get("page_state") or {}
            if not isinstance(page_state, dict):
                continue
            for key in merged:
                for item in page_state.get(key, []):
                    if not isinstance(item, dict):
                        continue
                    signature = (
                        str(item.get("role", "")).lower(),
                        str(item.get("name", "")).strip(),
                        str(item.get("value", "")).strip(),
                    )
                    if signature in seen:
                        continue
                    seen.add(signature)
                    merged[key].append(item)
        return merged

    @staticmethod
    def _inject_file_editor_content(payload: dict[str, Any], prior_results: dict[str, Any]) -> None:
        if payload.get("content") or payload.get("find_text") or payload.get("replace_text"):
            return

        synthesized = MissionOrchestrator._synthesize_file_editor_content(payload, prior_results)
        if synthesized:
            payload["content"] = synthesized
            return

        ordered = list(prior_results.values())[::-1]
        for result in ordered:
            if not isinstance(result, dict):
                continue
            if result.get("research_summary_markdown"):
                payload["content"] = result["research_summary_markdown"]
                return
            if result.get("snapshot_text"):
                content = result.get("summary", "").strip()
                if content:
                    payload["content"] = f"{content}\n\n{result['snapshot_text']}"
                else:
                    payload["content"] = result["snapshot_text"]
                return
            for key in (
                "analysis_markdown",
                "file_excerpt_markdown",
                "brief_markdown",
                "article_markdown",
                "scorecard_markdown",
                "stdout",
                "content",
                "summary",
            ):
                value = result.get(key)
                if isinstance(value, str) and value.strip():
                    payload["content"] = value
                    return

    def _execute_content_recovery_step(
        self,
        *,
        request: str,
        intent: TaskIntent,
        memory_context: str,
        critique_memory: dict[str, list[str]],
        failed_step,
        failed_payload: dict[str, Any],
        runtime_context: SkillExecutionContext,
        mission_id: str,
        attempt: int,
    ) -> StepExecutionResult | None:
        target = str(failed_payload.get("target_path") or "").replace("\\", "/")
        source_paths = [
            path
            for path in self._extract_request_paths(request)
            if path and path.replace("\\", "/") != target
        ]
        recovery_skill_name = "file-reader" if source_paths else "workspace-inspector"
        recovery_skill = self._registry.get(recovery_skill_name)
        if recovery_skill is None:
            return None

        recovery_step = PlanStep(
            id=f"{failed_step.id}_recovery_{attempt}",
            action="Recover missing file-editor content by gathering workspace evidence.",
            skill=recovery_skill_name,
            tool=recovery_skill_name,
            input_spec={"source_paths": source_paths} if source_paths else {},
            expected_output="Grounded evidence that can be used to synthesize editor content.",
            validation="Confirm evidence exists before retrying the failed write step.",
            depends_on=list(getattr(failed_step, "depends_on", [])),
            retry_limit=1,
            stop_on_failure=False,
            rollback_on_failure=False,
        )
        payload = self._build_step_payload(
            request=request,
            intent=intent,
            memory_context=memory_context,
            prior_results={},
            critique_memory=critique_memory,
            step=recovery_step,
        )
        trace = [
            "Critic recovery: file-editor was missing content.",
            f"Inserted `{recovery_skill_name}` before retrying `{failed_step.skill}`.",
            f"Attempt {attempt}: dispatching recovery skill `{recovery_skill_name}`.",
        ]
        try:
            output = self._workers.submit_task(
                self._execute_skill_task(
                    recovery_skill.name,
                    payload,
                    runtime_context,
                    mission_id=mission_id,
                    step_id=recovery_step.id,
                    attempt=attempt,
                )
            )
            validation = self._validator.validate_step(
                recovery_skill,
                output,
                recovery_step.expected_output,
                request,
                workspace_root=runtime_context.settings.workspace_root,
            )
            critic_review = self._critic_agent.review_step(request, recovery_step, output, validation.status)
            status = self._merge_status(validation.status, critic_review.status)
            trace.append(f"Recovery validation result: {validation.status.value}.")
            trace.extend(f"Critic: {note}" for note in critic_review.notes)
            return StepExecutionResult(
                step_id=recovery_step.id,
                skill=recovery_skill.name,
                tool=recovery_skill.name,
                status=status,
                output=output,
                evidence=self._extract_evidence(output),
                validation_status=validation.status,
                validation_notes=list(dict.fromkeys(validation.notes + critic_review.notes)),
                attempts=1,
                input_snapshot=self._input_snapshot(payload),
                trace=trace,
                agent_reviews=self._review_lines([critic_review]),
            )
        except Exception as exc:
            return StepExecutionResult(
                step_id=recovery_step.id,
                skill=recovery_skill.name,
                tool=recovery_skill.name,
                status=CompletionState.FAILED,
                output=None,
                evidence=[],
                validation_status=CompletionState.FAILED,
                validation_notes=[str(exc)],
                attempts=1,
                input_snapshot=self._input_snapshot(payload),
                trace=trace + [f"Recovery failed: {exc}"],
                error=str(exc),
            )

    @staticmethod
    def _is_missing_file_editor_content_failure(skill_name: str, exc: Exception) -> bool:
        return skill_name == "file-editor" and "requires explicit content" in str(exc).lower()

    @staticmethod
    def _extract_request_paths(request: str) -> list[str]:
        paths: list[str] = []
        for token in re.findall(r"[\w./\\:-]+", request, flags=re.UNICODE):
            cleaned = token.strip("`'\" ,:;()[]{}<>").replace("\\", "/")
            while cleaned.endswith((".", ",", ";", ":", "!", "?")) and len(cleaned) > 1:
                cleaned = cleaned[:-1]
            lowered = cleaned.lower()
            if len(cleaned) < 3 or cleaned.startswith(("http://", "https://")):
                continue
            if any(lowered.endswith(suffix) for suffix in (".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".md", ".txt", ".toml", ".yaml", ".yml", ".sql")):
                paths.append(cleaned)
        return list(dict.fromkeys(paths))

    @staticmethod
    def _synthesize_file_editor_content(payload: dict[str, Any], prior_results: dict[str, Any]) -> str:
        target = str(payload.get("target_path") or "").lower()
        request = str(payload.get("request") or "").lower()
        prior_text = MissionOrchestrator._prior_text(prior_results)
        if not prior_text.strip():
            return ""

        if target.endswith(".json"):
            import json as _json

            return _json.dumps({"summary": MissionOrchestrator._short_text(prior_text)}, ensure_ascii=False, indent=2)

        if "action item" in request or "action_items" in target or "todo" in request:
            bullets = MissionOrchestrator._extract_bullets(prior_text)
            if bullets:
                return "# Action Items\n" + "\n".join(f"- {item}" for item in bullets[:12])

        if target.endswith((".md", ".txt")):
            heading = "Summary" if "summary" in request else "Report"
            bullets = MissionOrchestrator._extract_bullets(prior_text)
            if bullets:
                return f"# {heading}\n\n" + "\n".join(f"- {item}" for item in bullets[:12])
            return f"# {heading}\n\n{MissionOrchestrator._short_text(prior_text)}"
        return ""

    @staticmethod
    def _prior_text(prior_results: dict[str, Any]) -> str:
        chunks: list[str] = []
        for result in prior_results.values():
            if not isinstance(result, dict):
                chunks.append(str(result))
                continue
            for key in (
                "file_excerpt_markdown",
                "analysis_markdown",
                "brief_markdown",
                "article_markdown",
                "scorecard_markdown",
                "content",
                "summary",
                "stdout",
            ):
                value = result.get(key)
                if isinstance(value, str) and value.strip():
                    chunks.append(value.strip())
        return "\n\n".join(chunks)

    @staticmethod
    def _extract_bullets(text: str) -> list[str]:
        bullets: list[str] = []
        in_action_section = False
        for raw_line in text.splitlines():
            line = raw_line.strip()
            lowered = line.lower()
            if "action items" in lowered or "action item" in lowered:
                in_action_section = True
                continue
            if in_action_section and line.startswith("#") and "action" not in lowered:
                break
            if line.startswith(("-", "*")):
                item = line.lstrip("-* ").strip()
                if item:
                    bullets.append(item)
        return list(dict.fromkeys(bullets))

    @staticmethod
    def _short_text(text: str, limit: int = 3000) -> str:
        compact = "\n".join(line.rstrip() for line in text.splitlines() if line.strip())
        return compact[:limit].rstrip()

    @staticmethod
    def _review_lines(reviews: list[AgentReview]) -> list[str]:
        lines: list[str] = []
        for review in reviews:
            lines.extend(f"{review.agent}: {note}" for note in review.notes)
        return lines

    def _restore_critique_memory(self, mission_id: str, step_results: list[StepExecutionResult]) -> dict[str, list[str]]:
        memory = self._audit_store.state_store.load_critique_memory(mission_id)
        for step in step_results:
            if step.agent_reviews:
                memory[step.step_id] = list(step.agent_reviews[:6])
                if step.skill:
                    memory[step.skill] = list(step.agent_reviews[:6])
        return memory

    def _remember_critique(
        self,
        mission_id: str,
        memory: dict[str, list[str]],
        step_id: str,
        skill_name: str,
        review: AgentReview,
    ) -> None:
        lines = [f"{review.agent}: {note}" for note in review.notes]
        memory[step_id] = lines[:6]
        memory[skill_name] = lines[:6]
        self._audit_store.state_store.save_critique_notes(mission_id, step_id, skill_name, lines[:6])

    @staticmethod
    def _merge_status(validation_status: CompletionState, critic_status: CompletionState) -> CompletionState:
        order = {
            CompletionState.FINISHED: 0,
            CompletionState.PARTIALLY_FINISHED: 1,
            CompletionState.NEEDS_RETRY: 2,
            CompletionState.NEEDS_HUMAN_CONFIRMATION: 3,
            CompletionState.FAILED: 4,
        }
        return critic_status if order[critic_status] > order[validation_status] else validation_status

    def _final_mission_status(self, step_results: list[StepExecutionResult], critic_status: CompletionState) -> CompletionState:
        if critic_status in {CompletionState.FAILED, CompletionState.NEEDS_HUMAN_CONFIRMATION}:
            return critic_status
        if any(step.status == CompletionState.NEEDS_HUMAN_CONFIRMATION for step in step_results):
            return CompletionState.NEEDS_HUMAN_CONFIRMATION
        if any(step.status == CompletionState.FAILED for step in step_results):
            return CompletionState.PARTIALLY_FINISHED if any(step.status == CompletionState.FINISHED for step in step_results) else CompletionState.FAILED
        if any(step.status == CompletionState.PARTIALLY_FINISHED for step in step_results):
            return CompletionState.PARTIALLY_FINISHED
        return CompletionState.FINISHED

    @staticmethod
    def _approval_checkpoint(step, payload: dict[str, Any], request: str, *, confirmed: bool) -> list[str]:
        if confirmed:
            return []

        notes: list[str] = []
        request_lower = request.lower()
        if step.skill == "file-editor":
            target = str(payload.get("target_path", "")).lower()
            if any(token in target for token in (".env", "secret", "credential", "token", "password", "key")):
                notes.append("Approval class: sensitive_file_write. This request writes sensitive data on the local machine and needs explicit approval.")

        if step.skill == "shell-executor":
            command = str(payload.get("command", "")).lower()
            if any(token in command for token in ("post ", "-x post", "--request post")):
                notes.append("Approval class: network_post. This request sends data outside the device via POST and needs explicit approval.")
            elif any(token in command for token in ("curl", "wget", "invoke-webrequest", "http://", "https://")):
                notes.append("Approval class: network_egress. This request sends or fetches data outside the device and needs explicit approval.")
            if any(token in command for token in ("rm ", "del ", "remove-item", "shutdown", "format", "drop ")):
                notes.append("Approval class: destructive_shell. This request performs an irreversible shell action and needs explicit approval.")

        if step.skill == "browser-executor":
            if any(token in request_lower for token in ("login", "signin", "password", "checkout", "purchase", "buy", "pay", "account")):
                notes.append("Approval class: authenticated_browser. This request sends data outside the device through an authenticated browser flow and needs explicit approval.")
            if any(token in request_lower for token in ("publish", "post live", "deploy", "submit form", "upload")):
                notes.append("Approval class: external_publish. This request publishes data outside the device and needs explicit approval.")

        if step.skill == "github-publisher":
            notes.append("Approval class: network_post. This request pushes content to GitHub and needs explicit approval.")

        if step.skill == "wordpress-publisher":
            notes.append("Approval class: external_publish. This request publishes content to WordPress and needs explicit approval.")

        if step.skill == "external-publisher":
            notes.append("Approval class: external_publish. This request sends data outside the device and needs explicit approval.")

        return notes

    @staticmethod
    def _format_trace_markdown(mission_trace: list[str], step_results: list[StepExecutionResult]) -> str:
        lines = ["# Mission Trace", ""]
        lines.extend(f"- {line}" for line in mission_trace)
        lines.append("")
        lines.append("# Step Results")
        for step in step_results:
            lines.append(f"- {step.step_id} | {step.tool or step.skill or 'reasoning'} | {step.status.value} | attempts={step.attempts}")
            for note in step.trace[:6]:
                lines.append(f"  {note}")
            for note in step.agent_reviews[:4]:
                lines.append(f"  agent: {note}")
            for note in step.rollback_notes:
                lines.append(f"  rollback: {note}")
        return "\n".join(lines)
