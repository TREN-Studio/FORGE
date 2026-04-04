from __future__ import annotations

from typing import TYPE_CHECKING

from forge.runtime.contracts import AgentReply, GatewayEnvelope, HeartbeatReport

if TYPE_CHECKING:
    from forge.runtime.agent import AgentRuntimeSettings, ForgeAgentRuntime

__all__ = [
    "AgentReply",
    "AgentRuntimeSettings",
    "ForgeAgentRuntime",
    "GatewayEnvelope",
    "HeartbeatReport",
]


def __getattr__(name: str):
    if name in {"AgentRuntimeSettings", "ForgeAgentRuntime"}:
        from forge.runtime.agent import AgentRuntimeSettings, ForgeAgentRuntime

        mapping = {
            "AgentRuntimeSettings": AgentRuntimeSettings,
            "ForgeAgentRuntime": ForgeAgentRuntime,
        }
        return mapping[name]
    raise AttributeError(name)
