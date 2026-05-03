"""
Explicit provider registry for FORGE.

This keeps provider wiring in one place so the session, CLI, and future
bootstrap logic all agree on the supported integrations.
"""

from __future__ import annotations

from importlib import import_module
import re

from forge.providers.base import BaseProvider

_PROVIDER_IMPORTS: tuple[tuple[str, str, str], ...] = (
    ("ollama", "forge.providers.ollama", "OllamaProvider"),
    ("groq", "forge.providers.groq", "GroqProvider"),
    ("gemini", "forge.providers.gemini", "GeminiProvider"),
    ("deepseek", "forge.providers.deepseek", "DeepSeekProvider"),
    ("openrouter", "forge.providers.openrouter", "OpenRouterProvider"),
    ("mistral", "forge.providers.mistral", "MistralProvider"),
    ("together", "forge.providers.together", "TogetherProvider"),
    ("nvidia", "forge.providers.nvidia", "NvidiaProvider"),
    ("cloudflare", "forge.providers.cloudflare", "CloudflareProvider"),
    ("anthropic", "forge.providers.anthropic", "AnthropicProvider"),
    ("openai", "forge.providers.openai", "OpenAIProvider"),
)

SPEED_TIMEOUTS: dict[str, float] = {
    "fast": 8.0,
    "normal": 15.0,
    "complex": 30.0,
}

MAX_PROGRESSIVE_ATTEMPTS = 3
PROGRESSIVE_TIMEOUT_WEIGHTS: tuple[float, ...] = (0.5, 0.3, 0.2)

_COMPLEX_PATTERNS = (
    r"\bthen\b",
    r"\brun (the )?tests?\b",
    r"\bunit tests?\b",
    r"\bmulti[- ]?step\b",
    r"\bcreate\b.*\b(test|report|project|files?)\b",
    r"\banaly[sz]e\b.*\bwrite\b",
    r"\bread\b.*\banaly[sz]e\b.*\bwrite\b",
)


def classify_speed(prompt: str) -> str:
    """Classify a request into a provider latency budget."""
    text = str(prompt or "").strip().lower()
    words = len(text.split())
    if not text:
        return "fast"
    if words <= 6:
        return "fast"
    if words > 30 or any(re.search(pattern, text) for pattern in _COMPLEX_PATTERNS):
        return "complex"
    return "normal"


def timeout_for_speed(speed: str) -> float:
    return SPEED_TIMEOUTS.get(speed, SPEED_TIMEOUTS["normal"])


def timeout_for_prompt(prompt: str) -> float:
    return timeout_for_speed(classify_speed(prompt))


def progressive_attempt_timeout(total_budget: float, attempt_index: int) -> float:
    """Split the total budget across up to three progressively shorter attempts."""
    budget = max(float(total_budget or SPEED_TIMEOUTS["normal"]), 0.001)
    index = min(max(attempt_index, 0), len(PROGRESSIVE_TIMEOUT_WEIGHTS) - 1)
    return max(0.001, budget * PROGRESSIVE_TIMEOUT_WEIGHTS[index])


def iter_provider_classes() -> list[type[BaseProvider]]:
    classes: list[type[BaseProvider]] = []
    for _, module_name, class_name in _PROVIDER_IMPORTS:
        try:
            module = import_module(module_name)
            classes.append(getattr(module, class_name))
        except (ImportError, AttributeError):
            continue
    return classes


def supported_provider_names() -> list[str]:
    return [name for name, _, _ in _PROVIDER_IMPORTS]


__all__ = [
    "BaseProvider",
    "MAX_PROGRESSIVE_ATTEMPTS",
    "SPEED_TIMEOUTS",
    "classify_speed",
    "iter_provider_classes",
    "progressive_attempt_timeout",
    "supported_provider_names",
    "timeout_for_prompt",
    "timeout_for_speed",
]
