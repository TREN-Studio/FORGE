"""
FORGE × Slack Integration
=========================
Implements Slack message broadcasting and channel queries.
"""

from __future__ import annotations

from typing import Any
import httpx

from forge.tools.base import ForgeTool, ToolResult


class SlackTool(ForgeTool):
    name = "slack"
    description = "Send messages and read channels in Slack"
    risk_class = "publish"
    requires_auth = ["slack_bot_token"]
    available_actions = ["post_message", "list_channels"]

    def __init__(self, token_resolver: Any | None = None) -> None:
        self._token_resolver = token_resolver

    async def execute(self, action: str, params: dict[str, Any]) -> ToolResult:
        # Obtain token from resolver or context parameters
        token = params.get("token")
        if not token and self._token_resolver:
            token = self._token_resolver("slack_bot_token")
        
        if not token:
            # Fallback mock for demo/testing
            if params.get("mock") or params.get("demo"):
                return ToolResult(
                    success=True,
                    data=f"Posted (Mock) to Slack channel {params.get('channel')}",
                    action_taken=f"Mocked post to Slack: {params.get('text')}",
                )
            return ToolResult(
                success=False,
                error="Slack bot token is not configured. Connect using: forge tools connect slack",
            )

        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"}

        try:
            if action == "post_message":
                channel = params.get("channel")
                text = params.get("text")
                if not channel or not text:
                    return ToolResult(success=False, error="channel and text parameters are required")

                async with httpx.AsyncClient() as client:
                    resp = await client.post(
                        "https://slack.com/api/chat.postMessage",
                        headers=headers,
                        json={"channel": channel, "text": text},
                    )
                data = resp.json()
                if data.get("ok"):
                    return ToolResult(success=True, data=f"Message posted to {channel}.", action_taken=f"Posted to Slack: {text}")
                return ToolResult(success=False, error=data.get("error", "Unknown Slack API error"))

            elif action == "list_channels":
                async with httpx.AsyncClient() as client:
                    resp = await client.get(
                        "https://slack.com/api/conversations.list",
                        headers=headers,
                    )
                data = resp.json()
                if data.get("ok"):
                    channels = [c.get("name", "") for c in data.get("channels", []) if c.get("name")]
                    return ToolResult(success=True, data=channels)
                return ToolResult(success=False, error=data.get("error", "Failed to list channels"))
            else:
                return ToolResult(success=False, error=f"Unsupported action: {action}")
        except Exception as exc:
            return ToolResult(success=False, error=str(exc))
