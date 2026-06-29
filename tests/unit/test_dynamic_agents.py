"""
FORGE Dynamic Multi-Agent System Tests
======================================
Verifies dynamic agent role allocation, spawning, and task execution workflows.

Run:
    python -m unittest tests.unit.test_dynamic_agents -v
"""

from __future__ import annotations

import unittest

from forge.brain.agent_factory import AgentFactory, SPECIALIZED_ROLES
from forge.brain.council import DynamicLLMAgent
from forge.core.session import ForgeSession


class TestAgentFactory(unittest.TestCase):
    """Verifies that the factory parses plan actions and returns expected specs."""

    def test_python_specialist_allocation(self) -> None:
        spec = AgentFactory.spawn_agent_for_step(
            step_id="step_1",
            skill_name="file-editor",
            action="Write a Python script that aggregates numbers",
        )
        self.assertEqual(spec.role_name, "Python Specialist")
        self.assertIn("Python Specialist", spec.instructions[0])
        self.assertIn("strict syntax check", "".join(spec.instructions).lower())

    def test_git_manager_allocation_on_publish(self) -> None:
        spec = AgentFactory.spawn_agent_for_step(
            step_id="step_2",
            skill_name="github-publisher",
            action="Push local repository changes to remote master",
        )
        self.assertEqual(spec.role_name, "Git & Repository Manager")

    def test_web_scraper_allocation_on_browser(self) -> None:
        spec = AgentFactory.spawn_agent_for_step(
            step_id="step_3",
            skill_name="browser-executor",
            action="Navigate to the documentation and extract list headings",
        )
        self.assertEqual(spec.role_name, "Web Scraper Specialist")

    def test_seo_auditor_allocation(self) -> None:
        spec = AgentFactory.spawn_agent_for_step(
            step_id="step_4",
            skill_name="browser-executor",
            action="Analyze the page's search optimization tags and meta keywords",
        )
        self.assertEqual(spec.role_name, "SEO Auditor")

    def test_bug_hunter_allocation(self) -> None:
        spec = AgentFactory.spawn_agent_for_step(
            step_id="step_5",
            skill_name="file-editor",
            action="Fix compilation errors and debug the unhandled traceback exception",
        )
        self.assertEqual(spec.role_name, "Bug Hunter")

    def test_generalist_as_fallback(self) -> None:
        spec = AgentFactory.spawn_agent_for_step(
            step_id="step_6",
            skill_name="unknown-skill",
            action="Do some general reasoning about the problem",
        )
        self.assertEqual(spec.role_name, "Generalist Assistant")

    def test_list_roles_returns_all_specialties(self) -> None:
        roles = AgentFactory.list_roles()
        self.assertEqual(len(roles), len(SPECIALIZED_ROLES))
        self.assertTrue(any(r["name"] == "WordPress Architect" for r in roles))


class TestDynamicLLMAgent(unittest.TestCase):
    """Verifies DynamicLLMAgent configuration and execution contract."""

    def setUp(self) -> None:
        # Dry session for testing config and prompt generation
        self.session = ForgeSession(memory=False)

    def test_dynamic_agent_initialization(self) -> None:
        agent = DynamicLLMAgent(
            role_name="Database Specialist",
            description="Expert in designing queries.",
            instructions=["Query local tables.", "Return schema."],
            session=self.session,
        )
        self.assertEqual(agent.role_name, "Database Specialist")
        self.assertEqual(agent.description, "Expert in designing queries.")
        self.assertEqual(len(agent.instructions), 2)


if __name__ == "__main__":
    unittest.main()
