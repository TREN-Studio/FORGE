# New tool integrations registry
from forge.tools.base import ForgeTool, ToolResult
from forge.tools.browser import ChromiumSemanticBrowser
from forge.tools.github import GitHubPublisher
from forge.tools.implementations.github_tool import GitHubTool
from forge.tools.implementations.google_workspace import GmailTool, GoogleDocsTool, GoogleSheetsTool
from forge.tools.implementations.notion_tool import NotionTool
from forge.tools.implementations.slack_tool import SlackTool
from forge.tools.publish import ExternalPublisher
from forge.tools.registry import ToolRegistry
from forge.tools.shell import GuardedShell
from forge.tools.wordpress import WordPressPublisher
from forge.tools.workspace import WorkspaceTools


def create_default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(GoogleDocsTool())
    registry.register(GoogleSheetsTool())
    registry.register(GmailTool())
    registry.register(SlackTool(token_resolver=registry.get_credential))
    registry.register(NotionTool(token_resolver=registry.get_credential))
    registry.register(GitHubTool(token_resolver=registry.get_credential))
    return registry


__all__ = [
    "ChromiumSemanticBrowser",
    "ExternalPublisher",
    "ForgeTool",
    "GitHubPublisher",
    "GuardedShell",
    "ToolRegistry",
    "ToolResult",
    "WordPressPublisher",
    "WorkspaceTools",
    "create_default_registry",
]
