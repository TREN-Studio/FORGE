"""
FORGE × Notion Integration
==========================
Implements page creations and database queries inside Notion.
"""

from __future__ import annotations

from typing import Any
import httpx

from forge.tools.base import ForgeTool, ToolResult


class NotionTool(ForgeTool):
    name = "notion"
    description = "Read and write to Notion databases and pages"
    risk_class = "write"
    requires_auth = ["notion_integration_token"]
    available_actions = ["create_page", "query_database"]

    def __init__(self, token_resolver: Any | None = None) -> None:
        self._token_resolver = token_resolver

    async def execute(self, action: str, params: dict[str, Any]) -> ToolResult:
        token = params.get("token")
        if not token and self._token_resolver:
            token = self._token_resolver("notion_integration_token")

        if not token:
            if params.get("mock") or params.get("demo"):
                return ToolResult(
                    success=True,
                    data="https://www.notion.so/mock-page-id",
                    action_taken="Mocked page creation in Notion.",
                )
            return ToolResult(
                success=False,
                error="Notion token not configured. Connect using: forge tools connect notion",
            )

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Notion-Version": "2022-06-28",
        }

        try:
            if action == "create_page":
                parent_id = params.get("parent_id")
                title = params.get("title", "FORGE Document")
                content = params.get("content", "")
                if not parent_id:
                    return ToolResult(success=False, error="parent_id parameter is required")

                payload = {
                    "parent": {"database_id": parent_id},
                    "properties": {
                        "Name": {
                            "title": [{"text": {"content": title}}]
                        }
                    },
                    "children": [
                        {
                            "object": "block",
                            "type": "paragraph",
                            "paragraph": {
                                "rich_text": [{"type": "text", "text": {"content": content}}]
                            }
                        }
                    ]
                }

                async with httpx.AsyncClient() as client:
                    resp = await client.post(
                        "https://api.notion.com/v1/pages",
                        headers=headers,
                        json=payload,
                    )
                data = resp.json()
                if resp.status_code == 200:
                    url = data.get("url", "https://notion.so")
                    return ToolResult(success=True, data=url, action_taken=f"Created Notion page: {title}")
                return ToolResult(success=False, error=data.get("message", "Notion API error"))

            elif action == "query_database":
                db_id = params.get("database_id")
                if not db_id:
                    return ToolResult(success=False, error="database_id parameter is required")

                async with httpx.AsyncClient() as client:
                    resp = await client.post(
                        f"https://api.notion.com/v1/databases/{db_id}/query",
                        headers=headers,
                    )
                data = resp.json()
                if resp.status_code == 200:
                    return ToolResult(success=True, data=data.get("results", []))
                return ToolResult(success=False, error=data.get("message", "Failed to query Notion database"))
            else:
                return ToolResult(success=False, error=f"Unsupported action: {action}")
        except Exception as exc:
            return ToolResult(success=False, error=str(exc))
