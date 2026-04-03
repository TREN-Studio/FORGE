"""
Provider registry for FORGE.

The session imports providers through this module so the runtime has a
single authoritative list of built-in integrations.
"""

from __future__ import annotations

from importlib import import_module

from forge.providers.base import BaseProvider

_PROVIDER_IMPORTS: tuple[tuple[str, str], ...] = (
    ("forge.providers.groq", "GroqProvider"),
    ("forge.providers.gemini", "GeminiProvider"),
    ("forge.providers.ollama", "OllamaProvider"),
    ("forge.providers.deepseek", "DeepSeekProvider"),
    ("forge.providers.openrouter", "OpenRouterProvider"),
)


def iter_provider_classes() -> list[type[BaseProvider]]:
    classes: list[type[BaseProvider]] = []
    for module_name, class_name in _PROVIDER_IMPORTS:
        try:
            module = import_module(module_name)
            classes.append(getattr(module, class_name))
        except (ImportError, AttributeError):
            continue
    return classes


__all__ = ["iter_provider_classes", "BaseProvider"]
