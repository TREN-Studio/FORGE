"""
FORGE Tool Registry and External Tools Integrations Tests
==========================================================
Verifies tool registration, encrypted credential saving/loading,
tool execution routing, and mock execution modes.

Run:
    python -m unittest tests.unit.test_tool_registry -v
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path

from forge.tools.base import ForgeTool, ToolResult
from forge.tools.registry import ToolRegistry
from forge.tools.implementations.google_workspace import GoogleDocsTool
from forge.tools.implementations.slack_tool import SlackTool
from forge.tools.implementations.notion_tool import NotionTool
from forge.tools.implementations.github_tool import GitHubTool


class MockTestTool(ForgeTool):
    name = "mock-test"
    description = "A mock tool for testing purposes"
    risk_class = "read"
    requires_auth = ["mock_key"]
    available_actions = ["say_hello"]

    async def execute(self, action: str, params: dict) -> ToolResult:
        if action == "say_hello":
            name = params.get("name", "World")
            return ToolResult(success=True, data=f"Hello, {name}!", action_taken="Said hello")
        return ToolResult(success=False, error=f"Action '{action}' not supported")


class TestToolRegistry(unittest.TestCase):
    """Verifies that ToolRegistry functions correctly."""

    def setUp(self) -> None:
        self.registry = ToolRegistry()
        self.mock_tool = MockTestTool()

    def test_register_and_get_tool(self) -> None:
        self.registry.register(self.mock_tool)
        fetched = self.registry.get("mock-test")
        self.assertEqual(fetched, self.mock_tool)

    def test_get_non_existent_tool_returns_none(self) -> None:
        self.assertIsNone(self.registry.get("non-existent-tool"))

    def test_credential_encryption(self) -> None:
        # Save a credentials key
        self.registry.set_credential("test_api_secret_key", "SuperSecret123")
        
        # Verify it is encrypted on disk
        if self.registry._creds_path.exists():
            encrypted_content = self.registry._creds_path.read_bytes()
            # The plain text must not exist in raw bytes
            self.assertNotIn(b"SuperSecret123", encrypted_content)
        
        # Verify it resolves correctly
        resolved = self.registry.get_credential("test_api_secret_key")
        self.assertEqual(resolved, "SuperSecret123")

    def test_has_credential_when_key_exists(self) -> None:
        self.registry.register(self.mock_tool)
        self.assertFalse(self.registry.has_credential("mock-test"))
        
        # Set mock key
        self.registry.set_credential("mock_key", "active-key-value")
        self.assertTrue(self.registry.has_credential("mock-test"))

    def test_disconnect_tool_removes_credentials(self) -> None:
        self.registry.register(self.mock_tool)
        self.registry.set_credential("mock_key", "secret-token")
        self.assertTrue(self.registry.has_credential("mock-test"))
        
        self.registry.disconnect_tool("mock-test")
        self.assertFalse(self.registry.has_credential("mock-test"))
        self.assertIsNone(self.registry.get_credential("mock_key"))


class TestToolExecution(unittest.IsolatedAsyncioTestCase):
    """Verifies tool actions execution contract."""

    async def test_mock_tool_execution(self) -> None:
        tool = MockTestTool()
        result = await tool.execute("say_hello", {"name": "Larbi"})
        self.assertTrue(result.success)
        self.assertEqual(result.data, "Hello, Larbi!")
        self.assertEqual(result.action_taken, "Said hello")

    async def test_slack_tool_mock_mode(self) -> None:
        tool = SlackTool()
        result = await tool.execute("post_message", {
            "channel": "#general",
            "text": "Hello world",
            "mock": True
        })
        self.assertTrue(result.success)
        self.assertIn("Mocked post to Slack", result.action_taken)

    async def test_notion_tool_mock_mode(self) -> None:
        tool = NotionTool()
        result = await tool.execute("create_page", {
            "parent_id": "some-db",
            "title": "FORGE Page",
            "content": "Notion Content",
            "mock": True
        })
        self.assertTrue(result.success)
        self.assertIn("notion.so", result.data)

    async def test_github_tool_mock_mode(self) -> None:
        tool = GitHubTool()
        result = await tool.execute("publish", {
            "owner": "TREN-Studio",
            "repo": "FORGE",
            "path": "README.md",
            "content": "# FORGE",
            "mock": True
        })
        self.assertTrue(result.success)
        self.assertIn("github.com", result.data)


if __name__ == "__main__":
    unittest.main()
