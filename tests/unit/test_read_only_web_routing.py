from __future__ import annotations

import unittest
from pathlib import Path

from forge.brain.contracts import IntentKind, RiskLevel
from forge.brain.orchestrator import MissionOrchestrator
from forge.brain.operator import ForgeOperator
from forge.config.settings import OperatorSettings


def _close_operator(operator: ForgeOperator) -> None:
    worker_runtime = getattr(operator.orchestrator, "_workers", None)
    if worker_runtime is not None:
        worker_runtime.close()
    operator.audit_store.state_store.close()
    MissionOrchestrator._shared_workers = None
    MissionOrchestrator._shared_approval_engine = None


class ReadOnlyWebRoutingTests(unittest.TestCase):
    def _routing_snapshot(self, request: str) -> dict[str, object]:
        operator = ForgeOperator(settings=OperatorSettings(enable_memory=False))
        try:
            intent = operator.intent_resolver.resolve(request)
            skills = operator.registry.list()
            routing = operator.skill_router.route(intent, skills)
            routing.selected_skills = operator._ordered_skill_names(routing.selected_skills)
            skill_lookup = {skill.name: skill for skill in skills}
            safety = operator.safety_guard.evaluate(request, intent, routing, skill_lookup)
            plan = operator.planner.build(intent, routing, safety, request=request)
            return {
                "intent": intent,
                "routing": routing,
                "safety": safety,
                "plan": plan,
            }
        finally:
            _close_operator(operator)

    def test_analyze_site_uses_browser_analysis_not_publish(self) -> None:
        snapshot = self._routing_snapshot("Analyze this site https://www.trenstudio.com/FORGE/")

        intent = snapshot["intent"]
        routing = snapshot["routing"]
        safety = snapshot["safety"]
        plan = snapshot["plan"]

        self.assertEqual(intent.primary_intent, IntentKind.RESEARCH)
        self.assertIn("browser-executor", routing.selected_skills)
        self.assertNotIn("external-publisher", routing.selected_skills)
        self.assertFalse(safety.blocked)
        self.assertFalse(safety.requires_confirmation)
        self.assertEqual(safety.risk_level, RiskLevel.LOW)
        self.assertEqual([step.skill for step in plan.steps], ["browser-executor"])
        self.assertNotIn("external-publisher", [step.skill for step in plan.steps])

    def test_read_only_website_review_does_not_request_approval(self) -> None:
        snapshot = self._routing_snapshot("Review this website https://www.trenstudio.com/FORGE/")

        safety = snapshot["safety"]
        plan = snapshot["plan"]

        self.assertFalse(safety.blocked)
        self.assertFalse(safety.requires_confirmation)
        self.assertEqual([step.skill for step in plan.steps], ["browser-executor"])

    def test_real_publish_request_still_requires_approval(self) -> None:
        snapshot = self._routing_snapshot("Publish this verified report to GitHub")

        routing = snapshot["routing"]
        safety = snapshot["safety"]
        plan = snapshot["plan"]

        self.assertIn("github-publisher", routing.selected_skills)
        self.assertTrue(safety.blocked)
        self.assertTrue(safety.requires_confirmation)
        self.assertEqual(safety.risk_level, RiskLevel.HIGH)
        self.assertIn("github-publisher", [step.skill for step in plan.steps])

    def test_internal_traces_are_not_in_primary_artifact_summary(self) -> None:
        summary = ForgeOperator._summarize_artifacts(
            {
                "mission_trace": ["raw trace must stay diagnostic-only"],
                "worker_lanes": {"lanes": ["raw worker lane state"]},
                "agent_reviews": [{"raw": True}],
                "browser-executor": {
                    "current_url": "https://www.trenstudio.com/FORGE/",
                    "title": "FORGE",
                    "page_state": {
                        "headings": [{"name": "FORGE"}],
                        "text": [
                            {"name": "AI content studio"},
                            {"name": "Create better social posts"},
                            {"name": "Plan, write, and publish"},
                        ],
                        "links": [{"name": "Pricing"}],
                        "buttons": [{"name": "Start now"}],
                    },
                },
            },
            [],
        )

        self.assertIn("Summary:", summary)
        self.assertIn("Strengths:", summary)
        self.assertNotIn("mission_trace", summary)
        self.assertNotIn("worker_lanes", summary)
        self.assertNotIn("agent_reviews", summary)
        self.assertNotIn("raw worker lane state", summary)

    def test_desktop_has_diagnostics_toggle(self) -> None:
        server_path = Path(__file__).resolve().parents[2] / "forge" / "desktop" / "server.py"
        source = server_path.read_text(encoding="utf-8")

        self.assertIn("diagnostics-toggle", source)
        self.assertIn("Show technical details", source)
        self.assertIn("JSON.stringify(diagnostics", source)


if __name__ == "__main__":
    unittest.main()
