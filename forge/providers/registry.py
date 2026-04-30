"""
Explicit provider registry for FORGE.

This keeps provider wiring in one place so the session, CLI, and future
bootstrap logic all agree on the supported integrations.
"""

from __future__ import annotations

from importlib import import_module

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


__all__ = ["iter_provider_classes", "supported_provider_names", "BaseProvider"]
