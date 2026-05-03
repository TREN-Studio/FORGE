from __future__ import annotations

from forge.core.identity import (
    FORGE_FILE_CAPABILITY_RESPONSE,
    FORGE_GREETING_RESPONSE,
    FORGE_IDENTITY_RESPONSE,
    FORGE_TEST_RESPONSE,
    asks_identity,
    enforce_forge_response_guard,
    instant_response,
)


INSTANT_RESPONSES = {
    "identity": FORGE_IDENTITY_RESPONSE,
    "file_capability": FORGE_FILE_CAPABILITY_RESPONSE,
    "hi": FORGE_GREETING_RESPONSE,
    "hello": FORGE_GREETING_RESPONSE,
    "hey": FORGE_GREETING_RESPONSE,
    "test": FORGE_TEST_RESPONSE,
    "ping": FORGE_TEST_RESPONSE,
}


def is_identity_question(prompt: str) -> bool:
    return asks_identity(prompt)


def sanitize_response(response: str) -> str:
    return enforce_forge_response_guard(response)


def get_instant_response(prompt: str) -> dict | None:
    content = instant_response(prompt)
    if content is None:
        return None

    kind = "instant_response"
    if content == FORGE_IDENTITY_RESPONSE:
        kind = "identity"
    elif content == FORGE_FILE_CAPABILITY_RESPONSE:
        kind = "file_capability"
    elif content == FORGE_GREETING_RESPONSE:
        kind = "greeting"
    elif content == FORGE_TEST_RESPONSE:
        kind = "health_check"

    return {
        "type": kind,
        "user_response": content,
        "content": content,
        "technical_details": {
            "source": "forge.identity_guard",
            "provider_call": False,
            "instant_response": True,
        },
    }
