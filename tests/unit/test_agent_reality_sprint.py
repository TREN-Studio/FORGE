from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from forge.brain.contracts import CompletionState, ExecutionClass, ExecutionPlan, IntentKind, PlanStep, RiskLevel, TaskIntent
from forge.brain.orchestrator import MissionOrchestrator
from forge.brain.operator import ForgeOperator
from forge.brain.planner import PlanningEngine
from forge.config.settings import OperatorSettings
from forge.core.models import ForgeResponse, Message, ModelSpec, ModelTier, TaskType
from forge.core.router import ForgeRouter
from forge.providers.base import BaseProvider
from forge.skills.runtime import SkillExecutionContext
from forge.safety.guard import SafetyDecision
from forge.skills.contracts import RoutingDecision
from forge.skills.router import SkillRouter
from forge.validation.json_validator import JSONValidationError, auto_repair_json, validate_json_strict


def _close_operator(operator: ForgeOperator) -> None:
    worker_runtime = getattr(operator.orchestrator, "_workers", None)
    if worker_runtime is not None:
        worker_runtime.close()
    operator.audit_store.state_store.close()
    MissionOrchestrator._shared_workers = None
    MissionOrchestrator._shared_approval_engine = None


class PlannerRealityTests(unittest.TestCase):
    def _intent(self, request: str) -> TaskIntent:
        return TaskIntent(
            raw_request=request,
            objective=request,
            primary_intent=IntentKind.AUTOMATION,
            intents=[IntentKind.AUTOMATION],
            execution_classes=[ExecutionClass.MULTI_SKILL_PIPELINE],
            risk_level=RiskLevel.LOW,
        )

    def _plan(self, request: str):
        routing = RoutingDecision(
            mode="pipeline",
            selected_skills=["file-editor"],
            fallback_skills=[],
            matches=[],
            reasons=[],
        )
        safety = SafetyDecision(
            risk_level=RiskLevel.LOW,
            blocked=False,
            requires_confirmation=False,
            use_dry_run=False,
            reasons=[],
        )
        return PlanningEngine().build(self._intent(request), routing, safety, request=request, max_steps=5)

    def test_multi_file_create_builds_one_editor_step_per_file(self) -> None:
        request = (
            "Create app/config.json with exactly this content:\n"
            "```json\n"
            '{"project":"forge-real-device","version":"1.0"}\n'
            "```\n"
            "Then create app/tasks.md with exactly this content:\n"
            "```markdown\n"
            "# Tasks\n"
            "- Verify workspace writes\n"
            "- Re-open outputs\n"
            "```"
        )

        plan = self._plan(request)
        editor_steps = [step for step in plan.steps if step.skill == "file-editor"]

        self.assertEqual([step.input_spec["target_path"] for step in editor_steps], ["app/config.json", "app/tasks.md"])
        self.assertIn("forge-real-device", editor_steps[0].input_spec["content"])
        self.assertIn("Verify workspace writes", editor_steps[1].input_spec["content"])

    def test_read_synthesize_write_places_reader_before_editor(self) -> None:
        request = (
            "Read input/brief.md, extract action items, and save them to reports/action_items.md."
        )

        plan = self._plan(request)
        self.assertGreaterEqual(len(plan.steps), 2)
        self.assertEqual(plan.steps[0].skill, "file-reader")
        self.assertEqual(plan.steps[1].skill, "file-editor")
        self.assertEqual(plan.steps[0].input_spec["source_paths"], ["input/brief.md"])
        self.assertNotIn("content", plan.steps[1].input_spec)

    def test_read_only_request_does_not_route_to_file_editor(self) -> None:
        request = "Analyze project/calc.py and report the bug. Do not edit files."
        operator = ForgeOperator(settings=OperatorSettings(enable_memory=False))
        intent = self._intent(request)
        intent.primary_intent = IntentKind.ANALYSIS
        intent.intents = [IntentKind.ANALYSIS]

        routing = SkillRouter(operator.settings).route(intent, operator.registry.list())

        self.assertNotIn("file-editor", routing.selected_skills)
        self.assertTrue(any(skill in routing.selected_skills for skill in ("file-reader", "codebase-analyzer", "workspace-inspector")))


class OperatorRealityTests(unittest.TestCase):
    def test_multi_file_task_creates_and_validates_both_outputs(self) -> None:
        with tempfile.TemporaryDirectory(prefix="forge-agent-reality-", ignore_cleanup_errors=True) as tmp:
            workspace = Path(tmp)
            operator = ForgeOperator(settings=OperatorSettings(enable_memory=False, workspace_root=workspace))
            try:
                request = (
                    "Create app/config.json with exactly this content:\n"
                    "```json\n"
                    "{project:forge-real-device,version:1.0}\n"
                    "```\n"
                    "Then create app/tasks.md with exactly this content:\n"
                    "```markdown\n"
                    "# Tasks\n"
                    "- Create config\n"
                    "- Validate JSON\n"
                    "```"
                )

                result = operator.handle(request, confirmed=True)

                self.assertEqual(result.validation_status, CompletionState.FINISHED, result.result)
                config_path = workspace / "app" / "config.json"
                tasks_path = workspace / "app" / "tasks.md"
                self.assertTrue(config_path.exists())
                self.assertTrue(tasks_path.exists())
                parsed = json.loads(config_path.read_text(encoding="utf-8"))
                self.assertEqual(parsed["project"], "forge-real-device")
                self.assertEqual(parsed["version"], 1.0)
                self.assertIn("Validate JSON", tasks_path.read_text(encoding="utf-8"))
            finally:
                _close_operator(operator)

    def test_read_synthesize_write_recovers_content_from_file_reader(self) -> None:
        with tempfile.TemporaryDirectory(prefix="forge-agent-reality-", ignore_cleanup_errors=True) as tmp:
            workspace = Path(tmp)
            (workspace / "input").mkdir()
            (workspace / "input" / "brief.md").write_text(
                "# Brief\n\n"
                "Action Items:\n"
                "- Add provider telemetry to the UI footer\n"
                "- Verify multi-file output after execution\n",
                encoding="utf-8",
            )
            operator = ForgeOperator(settings=OperatorSettings(enable_memory=False, workspace_root=workspace))
            try:
                result = operator.handle(
                    "Read input/brief.md, extract action items, and save them to reports/action_items.md.",
                    confirmed=True,
                )

                self.assertEqual(result.validation_status, CompletionState.FINISHED, result.result)
                report_path = workspace / "reports" / "action_items.md"
                self.assertTrue(report_path.exists())
                content = report_path.read_text(encoding="utf-8")
                self.assertIn("# Action Items", content)
                self.assertIn("Add provider telemetry", content)
                self.assertTrue(any(step.skill == "file-reader" for step in result.step_results))
                self.assertTrue(any(step.skill == "file-editor" for step in result.step_results))
            finally:
                _close_operator(operator)

    def test_wrong_first_file_editor_recovery_inserts_reader_then_retries(self) -> None:
        with tempfile.TemporaryDirectory(prefix="forge-agent-reality-", ignore_cleanup_errors=True) as tmp:
            workspace = Path(tmp)
            (workspace / "input").mkdir()
            (workspace / "input" / "brief.md").write_text(
                "# Brief\n\nAction Items:\n- Recover after premature editor choice\n",
                encoding="utf-8",
            )
            operator = ForgeOperator(settings=OperatorSettings(enable_memory=False, workspace_root=workspace))
            try:
                request = "Read input/brief.md and save action items to reports/action_items.md."
                intent = TaskIntent(
                    raw_request=request,
                    objective=request,
                    primary_intent=IntentKind.AUTOMATION,
                    intents=[IntentKind.AUTOMATION],
                    execution_classes=[ExecutionClass.MULTI_SKILL_PIPELINE],
                    risk_level=RiskLevel.LOW,
                )
                plan = ExecutionPlan(
                    objective=request,
                    task_type="automation",
                    risk_level=RiskLevel.LOW,
                    steps=[
                        PlanStep(
                            id="step_1",
                            action="Premature editor step used to exercise critic recovery.",
                            skill="file-editor",
                            tool="file-editor",
                            input_spec={"target_path": "reports/action_items.md", "edit_mode": "write"},
                            expected_output="A validated file mutation with synthesized content.",
                            validation="Confirm recovered evidence was used before writing.",
                            retry_limit=2,
                            stop_on_failure=True,
                            rollback_on_failure=True,
                        )
                    ],
                    completion_criteria=["Recover from missing file-editor content."],
                )
                mission_id, audit_log_path, resume_state = operator.audit_store.begin(request, plan)
                runtime_context = SkillExecutionContext(
                    settings=operator.settings,
                    session=operator.session,
                    memory=None,
                    dry_run=False,
                    sanitizer=operator.sanitizer,
                    state={"confirmed": True, "mission_id": mission_id},
                )

                mission = operator.orchestrator.execute(
                    request=request,
                    intent=intent,
                    plan=plan,
                    runtime_context=runtime_context,
                    mission_id=mission_id,
                    audit_log_path=audit_log_path,
                    resume_state=resume_state,
                    confirmed=True,
                )

                statuses = [step.status for step in mission.step_results]
                self.assertNotIn(CompletionState.FAILED, statuses)
                self.assertTrue(any(step.skill == "file-reader" for step in mission.step_results))
                self.assertTrue((workspace / "reports" / "action_items.md").exists())
                content = (workspace / "reports" / "action_items.md").read_text(encoding="utf-8")
                self.assertIn("Recover after premature editor choice", content)
            finally:
                _close_operator(operator)


class SlowProvider(BaseProvider):
    name = "slow"

    @property
    def models(self) -> list[ModelSpec]:
        return [
            ModelSpec(
                id="slow-model",
                provider=self.name,
                display_name="Slow Model",
                tier=ModelTier.FAST,
                strong_at=[TaskType.FAST],
                tags=["fast"],
            )
        ]

    async def complete(
        self,
        model: ModelSpec,
        messages: list[Message],
        max_tokens: int = 2048,
        temperature: float = 0.7,
    ) -> ForgeResponse:
        await asyncio.sleep(0.05)
        return ForgeResponse(content="too late", model_id=model.id, provider=self.name, latency_ms=50)


class FastProvider(BaseProvider):
    name = "fast"

    @property
    def models(self) -> list[ModelSpec]:
        return [
            ModelSpec(
                id="fast-model",
                provider=self.name,
                display_name="Fast Model",
                tier=ModelTier.FAST,
                strong_at=[TaskType.FAST],
                tags=["fast"],
            )
        ]

    async def complete(
        self,
        model: ModelSpec,
        messages: list[Message],
        max_tokens: int = 2048,
        temperature: float = 0.7,
    ) -> ForgeResponse:
        return ForgeResponse(
            content="ok",
            model_id=model.id,
            provider=self.name,
            latency_ms=3,
            input_tokens=1,
            output_tokens=1,
        )


class ProviderTelemetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_fallback_telemetry_records_attempts_and_final_provider(self) -> None:
        router = ForgeRouter()
        router.register(SlowProvider())
        router.register(FastProvider())
        router._scores["slow::slow-model"].quality_score = 1.0
        router._scores["fast::fast-model"].quality_score = 0.2

        response = await router.route(
            [Message(role="user", content="fast task")],
            task_type=TaskType.FAST,
            timeout=0.01,
        )

        telemetry = response.routing_telemetry
        self.assertEqual(response.provider, "fast")
        self.assertEqual(telemetry["selected_provider"], "slow/slow-model")
        self.assertEqual(telemetry["final_provider_used"], "fast/fast-model")
        self.assertEqual(telemetry["fallback_count"], 1)
        self.assertEqual(telemetry["attempted_providers"], ["slow/slow-model", "fast/fast-model"])
        self.assertIn("timeout", telemetry["timeout_reason"])


class JSONValidationTests(unittest.TestCase):
    def test_strict_json_rejects_bare_keys_and_repair_returns_valid_json(self) -> None:
        with self.assertRaises(JSONValidationError):
            validate_json_strict("{project:forge-real-device,version:1.0}")

        repaired = auto_repair_json("{project:forge-real-device,version:1.0}")
        parsed = json.loads(repaired)
        self.assertEqual(parsed["project"], "forge-real-device")
        self.assertEqual(parsed["version"], 1.0)


if __name__ == "__main__":
    unittest.main()
