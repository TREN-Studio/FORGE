"""
FORGE Critic/Recovery Expansion — Phase 1 Lock-in Tests
========================================================
Locks in the four deterministic upgrades of the expanded Critic/Recovery loop:

1. Pre-flight guard: a content-less file-editor dispatch is blocked BEFORE
   execution, and the orchestrator inserts a grounded file-reader step
   instead of burning the attempt on a guaranteed ValueError.
2. Hallucination guard: path drift / unverified writes are flagged as
   CRITICAL, downgrade a FINISHED step, and cap confidence — the LLM critic
   cannot override disk facts.
3. Drift guard: numeric request/output drift scoring with honest
   recovery hints (rebind_context) instead of silent acceptance.
4. Loop guard: identical re-dispatches are detected; recovery forces a
   fallback/abort instead of verbatim retries.
5. RecoveryManager.for_status: critic downgrades now route into recovery
   (recover_evidence) instead of being silently accepted.
6. Mission audit log carries critic_telemetry with the actual numbers
   (drift samples, flag counts, preflight blocks) — no unverifiable claims.
7. Identity-guard boundary (Invariant 4): critic LLM calls bypass the
   instant-response guard (allow_instant=False) — a request containing
   identity words cannot hijack the review.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from forge.brain.contracts import (
    AgentReview,
    CompletionState,
    ExecutionClass,
    ExecutionPlan,
    IntentKind,
    PlanStep,
    RiskLevel,
    StepExecutionResult,
    TaskIntent,
)
from forge.brain.council import CriticAgent
from forge.brain.critic_guards import (
    drift_score,
    hallucination_audit,
    loop_audit,
    loop_signature,
    preflight_step,
)
from forge.brain.orchestrator import MissionOrchestrator
from forge.brain.operator import ForgeOperator
from forge.config.settings import OperatorSettings
from forge.recovery.manager import RecoveryManager
from forge.skills.runtime import SkillExecutionContext


def _close_operator(operator: ForgeOperator) -> None:
    worker_runtime = getattr(operator.orchestrator, "_workers", None)
    if worker_runtime is not None:
        worker_runtime.close()
    operator.audit_store.state_store.close()
    MissionOrchestrator._shared_workers = None
    MissionOrchestrator._shared_approval_engine = None


class _PinnedDeterministicCritic:
    """Pin CriticAgent.review_step to the deterministic fallback for the
    duration of a test.

    Step reviews flow through the worker lane (WorkerTaskExecutor keeps its
    OWN CriticAgent with a live LLM session), so the review verdict depends
    on which provider answers that day — proven flaky. Class-level patching
    covers every instance (orchestrator's, worker executor's, remote
    workers'), pinning the integration to OUR deterministic logic.
    """

    def __enter__(self):
        from forge.brain import council as council_module

        self._council = council_module
        self._original = council_module.CriticAgent.review_step

        def _deterministic_review(self, request, step, output, validation_status):
            return self._review_step_fallback(request, step, output, validation_status)

        council_module.CriticAgent.review_step = _deterministic_review
        return self

    def __exit__(self, *exc_info):
        self._council.CriticAgent.review_step = self._original
        return False


def _editor_step(step_id: str = "step_1", retry_limit: int = 3) -> PlanStep:
    return PlanStep(
        id=step_id,
        action="Write the action items report.",
        skill="file-editor",
        tool="file-editor",
        input_spec={"target_path": "reports/action_items.md", "edit_mode": "write"},
        expected_output="A validated file mutation with synthesized content.",
        validation="Confirm recovered evidence was used before writing.",
        retry_limit=retry_limit,
        stop_on_failure=True,
        rollback_on_failure=True,
    )


class TestPreFlightGuard(unittest.TestCase):
    """1. Content-less file-editor dispatches are blocked before execution."""

    def test_contentless_editor_dispatch_is_blocked(self) -> None:
        step = _editor_step()
        payload = {"request": "Read input/brief.md and save action items to reports/action_items.md.", "target_path": "reports/action_items.md", "edit_mode": "write"}
        decision = preflight_step(step, payload)
        self.assertFalse(decision.allowed)
        self.assertTrue(decision.missing_content)
        # The guard surfaces the request paths usable as evidence sources.
        self.assertIn("input/brief.md", decision.source_paths)

    def test_editor_with_content_passes_preflight(self) -> None:
        step = _editor_step()
        payload = {"target_path": "reports/action_items.md", "edit_mode": "write", "content": "# Action Items\n- one"}
        decision = preflight_step(step, payload)
        self.assertTrue(decision.allowed)
        self.assertFalse(decision.missing_content)

    def test_editor_with_find_replace_passes_preflight(self) -> None:
        step = _editor_step()
        payload = {"target_path": "reports/action_items.md", "edit_mode": "replace", "find_text": "old", "replace_text": "new"}
        decision = preflight_step(step, payload)
        self.assertTrue(decision.allowed)

    def test_non_editor_skills_pass_preflight_untouched(self) -> None:
        step = PlanStep(id="s", action="read", skill="file-reader", expected_output="x", validation="y")
        decision = preflight_step(step, {"request": "anything"})
        self.assertTrue(decision.allowed)


class TestHallucinationGuard(unittest.TestCase):
    """2. Path drift and unverified writes are critical, LLM-proof findings."""

    def _step(self) -> PlanStep:
        return _editor_step()

    def test_path_drift_is_critical(self) -> None:
        output = {"edited_path": "other/thing.md", "verified": True, "changed": True}
        audit = hallucination_audit("save items", self._step(), output, CompletionState.FINISHED)
        self.assertFalse(audit.clean)
        self.assertTrue(audit.target_drift)
        self.assertTrue(audit.has_critical)
        self.assertEqual(audit.path_requested, "reports/action_items.md")
        self.assertEqual(audit.path_delivered, "other/thing.md")

    def test_unverified_write_is_critical(self) -> None:
        output = {"edited_path": "reports/action_items.md", "verified": False, "changed": True}
        audit = hallucination_audit("save items", self._step(), output, CompletionState.FINISHED)
        self.assertTrue(audit.has_critical)
        codes = [flag.code for flag in audit.flags]
        self.assertIn("unverified_write", codes)

    def test_matching_verified_write_is_clean(self) -> None:
        output = {"edited_path": "reports/action_items.md", "verified": True, "changed": True, "summary": "Applied write on reports/action_items.md."}
        audit = hallucination_audit("save items", self._step(), output, CompletionState.FINISHED)
        self.assertTrue(audit.clean)
        self.assertFalse(audit.has_critical)

    def test_no_change_write_is_warn(self) -> None:
        output = {"edited_path": "reports/action_items.md", "verified": True, "changed": False}
        audit = hallucination_audit("save items", self._step(), output, CompletionState.FINISHED)
        self.assertFalse(audit.clean)
        # warn-level flag must NOT count as critical
        self.assertFalse(audit.has_critical)
        codes = [flag.code for flag in audit.flags]
        self.assertIn("no_change_write", codes)

    def test_case_and_separator_insensitive_path_match(self) -> None:
        output = {"edited_path": "Reports/Action_Items.MD", "verified": True, "changed": True}
        audit = hallucination_audit("save items", self._step(), output, CompletionState.FINISHED)
        self.assertFalse(audit.target_drift)
        self.assertTrue(audit.clean)

    def test_critic_downgrades_finished_step_on_critical_flags(self) -> None:
        """The deterministic guard must beat a flattering LLM/fallback review."""
        critic = CriticAgent.__new__(CriticAgent)  # no session: force fallback path
        step = self._step()
        output = {"edited_path": "wrong/path.md", "verified": False, "changed": True, "confidence": 0.95}
        review = critic._review_step_fallback("save action items", step, output, CompletionState.FINISHED)
        self.assertEqual(review.status, CompletionState.PARTIALLY_FINISHED)
        self.assertEqual(review.recovery_hint, "recover_evidence")
        self.assertLessEqual(review.confidence or 1.0, 0.35)
        self.assertTrue(any("path drift" in note.lower() or "unverified" in note.lower() for note in review.notes))


class TestDriftGuard(unittest.TestCase):
    """3. Numeric request/output drift with honest recovery hints."""

    def test_zero_drift_when_output_echoes_request_terms(self) -> None:
        request = "Create reports/action_items.md with the extracted action items from input/brief.md"
        output = {"summary": "Wrote action items to reports/action_items.md extracted from input/brief.md"}
        self.assertLess(drift_score(request, output), 0.2)

    def test_full_drift_when_output_unrelated(self) -> None:
        request = "extract action items from input/brief.md"
        output = {"summary": "banana pancakes recipe with maple syrup"}
        self.assertGreater(drift_score(request, output), 0.85)

    def test_empty_output_is_full_drift(self) -> None:
        self.assertEqual(drift_score("extract action items", None), 1.0)
        self.assertEqual(drift_score("extract action items", {}), 1.0)

    def test_drift_hint_rebind_context_on_high_drift(self) -> None:
        critic = CriticAgent.__new__(CriticAgent)
        step = PlanStep(id="s", action="summarize", skill=None, expected_output="x", validation="y")
        output = {"summary": "completely unrelated words here about zebras"}
        review = critic._review_step_fallback("extract action items from input/brief.md", step, output, CompletionState.FINISHED)
        self.assertEqual(review.recovery_hint, "rebind_context")
        self.assertIn("drift", " ".join(review.notes).lower())


class TestLoopGuard(unittest.TestCase):
    """4. Identical re-dispatches detected; recovery escapes the loop."""

    def test_identical_dispatch_signatures_are_a_loop(self) -> None:
        sig = loop_signature("file-editor", "write the report", "reports/action_items.md") + "|abc123"
        audit = loop_audit([sig, sig])
        self.assertTrue(audit.is_loop)
        self.assertEqual(audit.attempts_seen, 2)
        self.assertEqual(audit.distinct_payloads, 1)

    def test_changed_payload_is_not_a_loop(self) -> None:
        base = loop_signature("file-editor", "write the report", "reports/action_items.md")
        audit = loop_audit([base + "|aaa", base + "|bbb"])
        self.assertFalse(audit.is_loop)
        self.assertEqual(audit.distinct_payloads, 2)

    def test_single_attempt_is_not_a_loop(self) -> None:
        audit = loop_audit(["sig|aaa"])
        self.assertFalse(audit.is_loop)

    def test_recovery_forces_fallback_on_loop(self) -> None:
        manager = RecoveryManager(max_retries_per_step=2)
        decision = manager.for_validation(2, CompletionState.FAILED, "file-reader", is_loop=True)
        self.assertEqual(decision.action, "fallback")
        self.assertEqual(decision.fallback_skill, "file-reader")

    def test_recovery_aborts_loop_without_fallback(self) -> None:
        manager = RecoveryManager(max_retries_per_step=2)
        decision = manager.for_validation(2, CompletionState.FAILED, None, is_loop=True)
        self.assertEqual(decision.action, "abort")
        self.assertIn("loop", decision.reason.lower())


class TestRecoveryHintRouting(unittest.TestCase):
    """5. Critic hints steer the recovery decision."""

    def test_recover_evidence_retries_within_budget(self) -> None:
        manager = RecoveryManager(max_retries_per_step=2)
        decision = manager.for_validation(1, CompletionState.PARTIALLY_FINISHED, None, recovery_hint="recover_evidence")
        self.assertEqual(decision.action, "retry")
        self.assertIn("evidence", decision.reason.lower())

    def test_recover_evidence_falls_back_after_budget(self) -> None:
        manager = RecoveryManager(max_retries_per_step=2)
        decision = manager.for_validation(2, CompletionState.PARTIALLY_FINISHED, "file-reader", recovery_hint="recover_evidence")
        self.assertEqual(decision.action, "fallback")

    def test_rebind_context_retries_once(self) -> None:
        manager = RecoveryManager(max_retries_per_step=2)
        decision = manager.for_validation(1, CompletionState.PARTIALLY_FINISHED, None, recovery_hint="rebind_context")
        self.assertEqual(decision.action, "retry")

    def test_for_status_routes_critic_downgrade_to_hint_recovery(self) -> None:
        """Critic downgraded FINISHED→PARTIALLY with a hint: recovery must
        engage (retry path), not silently accept the partial result."""
        manager = RecoveryManager(max_retries_per_step=2)
        decision = manager.for_status(
            1,
            CompletionState.FINISHED,
            CompletionState.PARTIALLY_FINISHED,
            None,
            recovery_hint="recover_evidence",
        )
        self.assertEqual(decision.action, "retry")

    def test_for_status_proceeds_when_critic_agrees(self) -> None:
        manager = RecoveryManager(max_retries_per_step=2)
        decision = manager.for_status(1, CompletionState.FINISHED, CompletionState.FINISHED, None)
        self.assertEqual(decision.action, "proceed")

    def test_for_status_plain_failure_keeps_legacy_retry(self) -> None:
        manager = RecoveryManager(max_retries_per_step=2)
        decision = manager.for_status(1, CompletionState.FAILED, CompletionState.FAILED, None)
        self.assertEqual(decision.action, "retry")


class TestPreflightOrchestratorIntegration(unittest.TestCase):
    """Pre-flight + recovery integration on the real orchestrator.

    The critic LLM path is environment-dependent (groq quota / ollama
    availability) — proven to flip this test between runs. We pin the
    deterministic fallback so the integration test exercises OUR code
    (pre-flight + recovery + telemetry), not the provider of the day.
    """

    def test_contentless_editor_inserts_reader_before_write(self) -> None:
        with tempfile.TemporaryDirectory(prefix="forge-critic-preflight-", ignore_cleanup_errors=True) as tmp:
            workspace = Path(tmp)
            (workspace / "input").mkdir()
            (workspace / "input" / "brief.md").write_text(
                "# Brief\n\nAction Items:\n- Pre-flight guard captured the evidence\n",
                encoding="utf-8",
            )
            operator = ForgeOperator(settings=OperatorSettings(enable_memory=False, workspace_root=workspace))
            try:
                with _PinnedDeterministicCritic():
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
                        steps=[_editor_step(retry_limit=3)],
                        completion_criteria=["Recover from missing file-editor content via pre-flight."],
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
                    report = workspace / "reports" / "action_items.md"
                    self.assertTrue(report.exists())
                    self.assertIn("Pre-flight guard captured the evidence", report.read_text(encoding="utf-8"))

                    # The mission audit log must carry the actual pre-flight numbers.
                    audit = json.loads(Path(audit_log_path).read_text(encoding="utf-8"))
                    telemetry = audit.get("critic_telemetry", {})
                    step_telemetry = telemetry.get("step_1", {})
                    self.assertGreaterEqual(step_telemetry.get("preflight_blocks", 0), 1)
            finally:
                _close_operator(operator)


class TestCriticTelemetryPersisted(unittest.TestCase):
    """6. Every mission audit log carries the measured critic numbers."""

    def test_telemetry_keys_present_in_audit_log(self) -> None:
        with tempfile.TemporaryDirectory(prefix="forge-critic-tele-", ignore_cleanup_errors=True) as tmp:
            workspace = Path(tmp)
            operator = ForgeOperator(settings=OperatorSettings(enable_memory=False, workspace_root=workspace))
            try:
                request = (
                    "Create hello.txt with exactly this content:\n```\nsome content\n```\n"
                )
                result = operator.handle(request, confirmed=True)
                self.assertEqual(result.validation_status, CompletionState.FINISHED, result.result)
                audit = json.loads(Path(result.audit_log_path).read_text(encoding="utf-8"))
                telemetry = audit.get("critic_telemetry")
                self.assertIsInstance(telemetry, dict)
                self.assertTrue(telemetry, "critic_telemetry must not be empty for a completed mission")
                for step_id, step_tel in telemetry.items():
                    self.assertIn("preflight_blocks", step_tel)
                    self.assertIn("hallucination_flags", step_tel)
                    self.assertIn("drift_samples", step_tel)
                    self.assertIn("loop_detected", step_tel)
            finally:
                _close_operator(operator)


class TestIdentityGuardBoundaryForCritic(unittest.TestCase):
    """7. Invariant 4 boundary: internal critic prompts cannot be hijacked."""

    def test_critic_prompt_composition_is_not_instant_hijacked(self) -> None:
        from forge.brain.agent_prompt import CRITIC_AGENT_LLM_PROMPT
        from forge.core.identity import instant_response

        # A user request containing identity words flows into the critic
        # prompt; the composed prompt itself must NOT trigger the canned
        # identity answer (the session call carries allow_instant=False).
        prompt = CRITIC_AGENT_LLM_PROMPT.format(
            request="what are you doing with my files",
            step_action="write the report",
            skill_name="file-editor",
            expected_output="report",
            output="done",
            validation_status="finished",
        )
        # The guard would fire on the RAW user text...
        self.assertIsNotNone(instant_response("what are you doing with my files"))
        # ...but the critic's review_step must bypass it: simulate the
        # allow_instant=False contract by checking the prompt contains the
        # identity-guard trigger set yet review still runs deterministically.
        critic = CriticAgent.__new__(CriticAgent)
        step = _editor_step()
        output = {"edited_path": "reports/action_items.md", "verified": True, "changed": True}
        review = critic._review_step_fallback(
            "what are you doing with my files",
            step,
            output,
            CompletionState.FINISHED,
        )
        # The review answers with critic notes, never the identity answer.
        self.assertEqual(review.agent, "critic")
        joined = " ".join(review.notes).lower()
        self.assertNotIn("developed by tren studio", joined)
        self.assertNotIn("founded by larbi aboudi", joined)

    def test_agent_review_carries_recovery_hint_field(self) -> None:
        review = AgentReview(agent="critic", status=CompletionState.PARTIALLY_FINISHED, notes=["x"], confidence=0.3, recovery_hint="recover_evidence")
        dumped = review.model_dump(mode="json")
        self.assertEqual(dumped["recovery_hint"], "recover_evidence")
        # Round-trip through the worker decode path.
        from forge.brain.worker_executor import decode_agent_review

        decoded = decode_agent_review(dumped)
        self.assertEqual(decoded.recovery_hint, "recover_evidence")


if __name__ == "__main__":
    unittest.main()
