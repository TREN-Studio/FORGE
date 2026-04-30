from __future__ import annotations

from typing import Any

from forge.brain.composer import ResponseComposer
from forge.brain.contracts import CompletionState, ExecutionPlan, IntentKind, OperatorResult, StepExecutionResult
from forge.brain.mission_store import MissionAuditStore
from forge.brain.intent import IntentResolver
from forge.brain.orchestrator import MissionOrchestrator
from forge.brain.planner import PlanningEngine
from forge.brain.prompt import CORE_BRAIN_PROMPT
from forge.config.settings import OperatorSettings
from forge.core.session import ForgeSession
from forge.memory.context import ContextMemory
from forge.recovery.manager import RecoveryManager
from forge.safety.guard import SafetyDecision, SafetyGuard
from forge.safety.sanitizer import PromptInjectionFirewall
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
        provider_secrets: dict[str, dict[str, str]] | None = None,
    ) -> None:
        self.settings = settings or OperatorSettings()
        self.session = session or ForgeSession(
            system_prompt=CORE_BRAIN_PROMPT,
            memory=self.settings.enable_memory,
            provider_secrets=provider_secrets,
            allow_host_fallback=provider_secrets is None,
        )
        self.memory = ContextMemory(self.session._memory) if self.session._memory else None
        self.registry = SkillRegistry(self.settings)
        self.registry.refresh()
        self.intent_resolver = IntentResolver()
        self.skill_router = SkillRouter(self.settings)
        self.safety_guard = SafetyGuard(self.settings)
        self.sanitizer = PromptInjectionFirewall(max_chars=self.settings.prompt_injection_max_chars)
        self.planner = PlanningEngine()
        self.runtime = SkillRuntime()
        self.validator = ResultValidator()
        self.recovery = RecoveryManager(max_retries_per_step=self.settings.max_retries_per_step)
        self.audit_store = MissionAuditStore(self.settings)
        self.orchestrator = MissionOrchestrator(
            self.registry,
            self.runtime,
            self.validator,
            self.recovery,
            self.audit_store,
            compact_prior_results=self._compact_prior_results,
            extract_evidence=self._extract_evidence,
        )
        self.composer = ResponseComposer()

    def handle(
        self,
        request: str,
        confirmed: bool = False,
        dry_run: bool = False,
        resume_mission_id: str | None = None,
        memory_context_override: str | None = None,
    ) -> OperatorResult:
        if memory_context_override is not None:
            memory_context = memory_context_override
        else:
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
        plan = self.planner.build(intent, routing, safety, request=request, max_steps=self.settings.max_plan_steps)
        mission_id, audit_log_path, resume_state = self.audit_store.begin(
            request,
            plan,
            resume_mission_id=resume_mission_id,
        )

        if intent.primary_intent == IntentKind.CONVERSATION and not routing.selected_skills:
            return self._conversation_result(
                request=request,
                intent=intent,
                routing=routing,
                mission_id=mission_id,
                audit_log_path=audit_log_path,
                resumed_from_step=resume_state.resumed_from_step if resume_state else None,
            )

        if safety.blocked:
            status = CompletionState.NEEDS_HUMAN_CONFIRMATION if safety.requires_confirmation else CompletionState.FAILED
            self.audit_store.save_progress(
                mission_id,
                audit_log_path,
                request=request,
                plan=plan,
                status=status.value,
                step_results=[],
                artifacts={"mission_audit": {"mission_id": mission_id, "audit_log_path": audit_log_path}},
                mission_trace=["Execution blocked in SafetyGuard before any skill ran."],
                resumed_from_step=resume_state.resumed_from_step if resume_state else None,
            )
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
                mission_id=mission_id,
                audit_log_path=audit_log_path,
                resumed_from_step=resume_state.resumed_from_step if resume_state else None,
            )

        runtime_context = SkillExecutionContext(
            settings=self.settings,
            session=self.session,
            memory=self.memory,
            dry_run=safety.use_dry_run,
            sanitizer=self.sanitizer,
            state={"memory_context": memory_context, "confirmed": confirmed, "mission_id": mission_id},
        )

        mission = self.orchestrator.execute(
            request=request,
            intent=intent,
            plan=plan,
            runtime_context=runtime_context,
            mission_id=mission_id,
            audit_log_path=audit_log_path,
            resume_state=resume_state,
            confirmed=confirmed,
            memory_context=memory_context,
            remember_execution=(
                (lambda skill_name, note: self.memory.remember_execution(skill_name, note))
                if self.memory and not safety.use_dry_run
                else None
            ),
        )

        final_status = self.validator.evaluate_plan(plan, mission.step_results)
        result_text = self._summarize_artifacts(mission.artifacts, mission.step_results)
        risks = list(dict.fromkeys(safety.reasons + self._step_risks(mission.step_results)))
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
            step_results=mission.step_results,
            artifacts=mission.artifacts,
            mission_trace=mission.mission_trace,
            mission_id=mission.mission_id,
            audit_log_path=mission.audit_log_path,
            resumed_from_step=mission.resumed_from_step,
            agent_reviews=mission.agent_reviews,
        )

    def handle_as_text(
        self,
        request: str,
        confirmed: bool = False,
        dry_run: bool = False,
        resume_mission_id: str | None = None,
        memory_context_override: str | None = None,
    ) -> str:
        return self.composer.compose(
            self.handle(
                request,
                confirmed=confirmed,
                dry_run=dry_run,
                resume_mission_id=resume_mission_id,
                memory_context_override=memory_context_override,
            )
        )

    def _clarification_result(
        self,
        request: str,
        intent,
        routing,
        plan: ExecutionPlan,
        mission_id: str,
        audit_log_path: str,
        resumed_from_step: str | None,
    ) -> OperatorResult:
        clarification = self._clarification_text(request)
        mission_trace = [
            "Intent resolved as conversation-only.",
            "Raw chat fallback was blocked.",
            "FORGE requested a concrete mission before execution.",
        ]
        result = OperatorResult(
            objective="Clarify the mission before execution.",
            approach_taken=[
                f"Intent resolved as `{intent.primary_intent.value}`.",
                f"Routing mode: `{routing.mode}`.",
                "Blocked raw chat behavior and switched to operator clarification.",
            ],
            result=clarification,
            validation_status=CompletionState.PARTIALLY_FINISHED,
            risks_or_limitations=[
                "The request did not specify a concrete, verifiable task.",
                "FORGE will not pretend to execute when no actionable mission exists.",
            ],
            best_next_action="State one concrete mission with a target, expected result, or artifact.",
            intent=intent,
            plan=plan,
            step_results=[],
            artifacts={},
            mission_trace=mission_trace,
            mission_id=mission_id,
            audit_log_path=audit_log_path,
            resumed_from_step=resumed_from_step,
            agent_reviews=[],
        )
        self.audit_store.save_progress(
            mission_id,
            audit_log_path,
            request=request,
            plan=plan,
            status=result.validation_status.value,
            step_results=[],
            artifacts={},
            mission_trace=mission_trace,
            resumed_from_step=resumed_from_step,
        )
        return result

    def _conversation_result(
        self,
        request: str,
        intent,
        routing,
        mission_id: str,
        audit_log_path: str,
        resumed_from_step: str | None,
    ) -> OperatorResult:
        plan = ExecutionPlan(
            objective=intent.objective or "Answer the user directly.",
            task_type=intent.task_type,
            risk_level=intent.risk_level,
            steps=[],
            fallbacks=[],
            completion_criteria=["Return a natural, direct answer without fake execution."],
        )
        try:
            reply_response = self.session.ask_response(request, task_type=intent.task_type, remember=False)
            reply = reply_response.content
            status = CompletionState.FINISHED
            risks: list[str] = []
            best_next_action = "Continue the conversation or give FORGE a concrete task to execute."
            mission_trace = [
                "Intent resolved as conversation.",
                "No tools were required.",
                "FORGE selected the strongest available model path for a direct reply.",
            ]
            artifacts = {
                "conversation_metadata": {
                    "model_id": reply_response.model_id,
                    "provider": reply_response.provider,
                    "latency_ms": reply_response.latency_ms,
                    "total_tokens": reply_response.total_tokens,
                }
            }
        except Exception as exc:
            reply = self._clarification_text(request)
            status = CompletionState.PARTIALLY_FINISHED
            risks = [str(exc)]
            best_next_action = "Add a working provider key or give FORGE an executable task inside a selected workspace."
            mission_trace = [
                "Intent resolved as conversation.",
                "Direct model reply failed.",
                "FORGE returned a safe fallback clarification instead of pretending success.",
            ]
            artifacts = {}

        result = OperatorResult(
            objective=intent.objective or "Answer the user directly.",
            approach_taken=[
                f"Intent resolved as `{intent.primary_intent.value}`.",
                "No execution skills were needed.",
                "FORGE used direct model routing for a natural reply.",
            ],
            result=reply,
            validation_status=status,
            risks_or_limitations=risks,
            best_next_action=best_next_action,
            intent=intent,
            plan=plan,
            step_results=[],
            artifacts=artifacts,
            mission_trace=mission_trace,
            mission_id=mission_id,
            audit_log_path=audit_log_path,
            resumed_from_step=resumed_from_step,
            agent_reviews=[],
        )
        self.audit_store.save_progress(
            mission_id,
            audit_log_path,
            request=request,
            plan=plan,
            status=result.validation_status.value,
            step_results=[],
            artifacts={},
            mission_trace=mission_trace,
            resumed_from_step=resumed_from_step,
        )
        return result

    @staticmethod
    def _clarification_text(request: str) -> str:
        if any("\u0600" <= char <= "\u06ff" for char in request):
            return (
                "FORGE ليس chatbot عاماً. هذا الطلب لا يحتوي على مهمة قابلة للتنفيذ بعد.\n\n"
                "أعطني أمراً واحداً واضحاً مثل:\n"
                "- افحص هذا الحاسوب واذكر النظام والرام والمعالج\n"
                "- حلل هذا المشروع واحفظ تقريراً في ملف\n"
                "- افتح هذا الرابط واستخرج النقاط الرئيسية\n"
                "- عدل هذا الملف ثم شغّل الاختبار\n"
                "- انشر هذا التقرير إلى GitHub أو WordPress بعد التأكيد\n\n"
                "عندها سينفذ FORGE المهمة كـ agent بخطة وخطوات وتحقيق ونتيجة قابلة للتحقق."
            )
        return (
            "FORGE is not a general chatbot. This request does not define an executable mission yet.\n\n"
            "Give one concrete instruction such as:\n"
            "- Inspect this computer and report the OS, RAM, and CPU\n"
            "- Analyze this project and save a report file\n"
            "- Open this URL and extract the key findings\n"
            "- Edit this file and run the test command\n"
            "- Publish this verified report to GitHub or WordPress after confirmation\n\n"
            "Then FORGE will execute it as an agent with a plan, steps, evidence, and validation."
        )

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
        if len(getattr(intent, "intents", [])) > 1:
            lines.append("Mission decomposed into multiple sub-tasks.")
        if safety.use_dry_run:
            lines.append("Executed in dry-run mode.")
        return lines

    @staticmethod
    def _ordered_skill_names(skill_names: list[str]) -> list[str]:
        def priority(name: str) -> tuple[int, str]:
            lowered = name.lower()
            if "inspector" in lowered or "research" in lowered or "analyzer" in lowered:
                return (10, lowered)
            if "browser" in lowered:
                return (30, lowered)
            if "editor" in lowered:
                return (40, lowered)
            if "shell" in lowered:
                return (60, lowered)
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
            elif "edited_path" in result:
                compact[skill_name] = {
                    "summary": result.get("summary"),
                    "edited_path": result.get("edited_path"),
                    "operation": result.get("operation"),
                    "diff": result.get("diff"),
                }
            elif "command" in result:
                compact[skill_name] = {
                    "summary": result.get("summary"),
                    "command": result.get("command"),
                    "exit_code": result.get("exit_code"),
                    "stdout": result.get("stdout"),
                    "stderr": result.get("stderr"),
                }
            elif "page_state" in result:
                compact[skill_name] = {
                    "summary": result.get("summary"),
                    "current_url": result.get("current_url"),
                    "title": result.get("title"),
                    "action_trace": result.get("action_trace"),
                    "snapshot_text": result.get("snapshot_text"),
                    "research_summary_markdown": result.get("research_summary_markdown"),
                    "verification": result.get("verification"),
                    "confidence": result.get("confidence"),
                }
            elif "summary" in result:
                compact[skill_name] = {
                    "summary": result.get("summary"),
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
                    if "analysis_markdown" in value:
                        lines.append(str(value["analysis_markdown"]))
                    elif "file_excerpt_markdown" in value:
                        lines.append(str(value["file_excerpt_markdown"]))
                    elif "brief_markdown" in value:
                        lines.append(str(value["brief_markdown"]))
                    elif "article_markdown" in value:
                        lines.append(str(value["article_markdown"]))
                    elif "scorecard_markdown" in value:
                        lines.append(str(value["scorecard_markdown"]))
                    elif "page_state" in value or "snapshot_text" in value:
                        lines.append(str(value.get("summary") or value.get("current_url") or "Browser step completed."))
                        if value.get("research_summary_markdown"):
                            lines.append(str(value["research_summary_markdown"]))
                        if value.get("action_trace"):
                            lines.append(str(value["action_trace"]))
                        if value.get("snapshot_text"):
                            lines.append(str(value["snapshot_text"]))
                    elif "mission_id" in value and "audit_log_path" in value:
                        lines.append(f"Mission ID: {value.get('mission_id')}")
                        lines.append(f"Audit log: {value.get('audit_log_path')}")
                    elif "lanes" in value:
                        raw_lanes = value.get("lanes", [])
                        lane_lines: list[str] = []
                        if isinstance(raw_lanes, dict):
                            for service in raw_lanes.get("services", [])[:6]:
                                lane_lines.append(
                                    f"{service.get('service')}: status={service.get('status')} "
                                    f"processed={sum(int(worker.get('processed_jobs', 0)) for worker in service.get('workers', []))} "
                                    f"queued={service.get('queued_jobs', 0)}"
                                )
                        else:
                            for lane in raw_lanes[:6]:
                                lane_lines.append(
                                    f"{lane.get('lane_id')}: processed={lane.get('processed_jobs')} queued={lane.get('queued_jobs')}"
                                )
                        lines.append("Worker lanes -> " + "; ".join(lane_lines))
                    elif "diff" in value and value.get("summary"):
                        lines.append(str(value["summary"]))
                        if value["diff"]:
                            lines.append(str(value["diff"]))
                    elif "command" in value:
                        lines.append(str(value.get("summary") or value["command"]))
                        if value.get("stdout"):
                            lines.append(str(value["stdout"]))
                        if value.get("stderr"):
                            lines.append(str(value["stderr"]))
                    elif "summary" in value:
                        lines.append(str(value["summary"]))
                    elif "trace_markdown" in value:
                        lines.append(str(value["trace_markdown"]))
                    else:
                        lines.append(str({k: v for k, v in value.items() if k != "payload_preview"}))
                else:
                    lines.append(str(value))
            return "\n\n".join(lines)
        if step_results:
            if step_results[-1].output is None:
                return step_results[-1].error or "No output produced."
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
        if output.get("edited_path"):
            evidence.append(f"edited:{output['edited_path']}")
        if output.get("command"):
            evidence.append(f"command:{output['command']}")
        if output.get("current_url"):
            evidence.append(f"url:{output['current_url']}")
        return list(dict.fromkeys(evidence))
