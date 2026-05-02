from __future__ import annotations

import json
import os
import unittest

from forge.brain.contracts import IntentKind, TaskIntent
from forge.config.settings import OperatorSettings
from forge.skills.registry import SkillMeta, SkillRegistry
from forge.skills.router import SkillRouter


def _intent(request: str, primary: IntentKind = IntentKind.AUTOMATION) -> TaskIntent:
    return TaskIntent(
        raw_request=request,
        objective=request,
        primary_intent=primary,
        intents=[primary],
        requested_output="clean final response",
    )


class SkillRegistryGovernanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = OperatorSettings(enable_memory=False)
        self.registry = SkillRegistry(self.settings)
        self.registry.refresh()

    def test_all_existing_skills_have_governance_schema(self) -> None:
        required = {
            "id",
            "tier",
            "risk_class",
            "visibility",
            "preconditions",
            "allowlist",
            "trigger_rules",
            "built_in",
        }
        for skill_dir in self.settings.bundled_skills_root.iterdir():
            if not skill_dir.is_dir() or skill_dir.name.startswith("_"):
                continue
            schema_path = skill_dir / "schema.json"
            self.assertTrue(schema_path.exists(), f"{skill_dir.name} is missing schema.json")
            schema = json.loads(schema_path.read_text(encoding="utf-8-sig"))
            self.assertFalse(required.difference(schema), f"{skill_dir.name} schema is missing governance fields")

    def test_file_editor_requires_content(self) -> None:
        """file-editor is not selected when new_content is missing."""
        intent = _intent("Update app/config.json after reviewing it.")

        registry_candidates = [skill.id for skill in self.registry.skills_for_intent(intent)]
        routing = SkillRouter(self.settings, registry=self.registry).route(intent, self.registry.list())

        self.assertNotIn("file-editor", registry_candidates)
        self.assertNotIn("file-editor", routing.selected_skills)

    def test_tier4_disabled_by_default(self) -> None:
        """Tier 4 skills return None unless FORGE_GATED=true."""
        previous = os.environ.pop("FORGE_GATED", None)
        try:
            self.registry._skill_meta["destructive-ops"] = SkillMeta(
                id="destructive-ops",
                tier=4,
                risk_class="admin",
                visibility="disabled",
                preconditions=["authorization"],
                allowlist=["operator"],
                trigger_rules=["delete everything"],
                built_in=False,
            )

            self.assertIsNone(self.registry.get_skill("destructive-ops", "operator"))
            os.environ["FORGE_GATED"] = "true"
            self.assertIsNotNone(self.registry.get_skill("destructive-ops", "operator"))
        finally:
            if previous is None:
                os.environ.pop("FORGE_GATED", None)
            else:
                os.environ["FORGE_GATED"] = previous

    def test_read_only_intent_no_write_skills(self) -> None:
        """Analyze-only tasks do not bring write skills into the route."""
        intent = _intent("Analyze app/config.json and summarize it. Do not edit files.", IntentKind.ANALYSIS)

        registry_candidates = self.registry.skills_for_intent(intent)
        routing = SkillRouter(self.settings, registry=self.registry).route(intent, self.registry.list())

        self.assertTrue(registry_candidates)
        self.assertTrue(all(skill.risk_class == "read" for skill in registry_candidates))
        self.assertNotIn("file-editor", routing.selected_skills)
        self.assertFalse(any(skill in routing.selected_skills for skill in ("artifact-writer", "seo-article-writer")))


if __name__ == "__main__":
    unittest.main()
