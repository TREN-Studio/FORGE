from __future__ import annotations

from forge.core.identity import (
    FORGE_FILE_CAPABILITY_RESPONSE,
    FORGE_IDENTITY_RESPONSE,
    FORGE_IDENTITY_SYSTEM_INSTRUCTION,
    asks_file_capability,
    asks_identity,
    enforce_agent_capability_guard,
    enforce_forge_response_guard,
    enforce_identity_guard,
)

__all__ = [
    "FORGE_FILE_CAPABILITY_RESPONSE",
    "FORGE_IDENTITY_RESPONSE",
    "FORGE_IDENTITY_SYSTEM_INSTRUCTION",
    "asks_file_capability",
    "asks_identity",
    "enforce_agent_capability_guard",
    "enforce_forge_response_guard",
    "enforce_identity_guard",
]
