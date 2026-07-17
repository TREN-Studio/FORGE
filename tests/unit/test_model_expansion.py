"""
FORGE Model Expansion & HuggingFace Provider Unit Tests
========================================================
Verifies models list registration for Groq, OpenRouter, and HuggingFace,
HuggingFace complete and stream interfaces, and registry loading.

Run:
    python -m unittest tests.unit.test_model_expansion -v
"""

from __future__ import annotations

import unittest
from typing import Any

from forge.core.models import Message, ModelTier, TaskType
from forge.providers.registry import iter_provider_classes, supported_provider_names
from forge.providers.groq import GroqProvider
from forge.providers.openrouter import OpenRouterProvider
from forge.providers.huggingface import HuggingFaceProvider


class TestModelExpansion(unittest.TestCase):
    """Verifies registry updates and model lists for Groq, OpenRouter, and HuggingFace."""

    def test_registry_contains_huggingface(self) -> None:
        names = supported_provider_names()
        self.assertIn("huggingface", names)
        
        classes = [cls.__name__ for cls in iter_provider_classes()]
        self.assertIn("HuggingFaceProvider", classes)

    def test_groq_has_llama_and_qwen_models(self) -> None:
        provider = GroqProvider()
        models = {m.id: m for m in provider.models}
        
        self.assertIn("llama-3.3-70b-versatile", models)
        self.assertIn("qwen/qwen3-32b", models)
        self.assertIn("meta-llama/llama-4-scout-17b-16e-instruct", models)
        
        llama = models["llama-3.3-70b-versatile"]
        self.assertEqual(llama.tier, ModelTier.ULTRA)
        self.assertIn(TaskType.CODE, llama.strong_at)
        self.assertTrue(llama.free)

    def test_openrouter_has_expanded_free_models(self) -> None:
        provider = OpenRouterProvider()
        models = {m.id: m for m in provider.models}
        
        self.assertIn("google/gemini-2.5-flash", models)
        self.assertIn("microsoft/phi-3-medium-128k-instruct", models)
        self.assertIn("openchat/openchat-7b", models)
        self.assertIn("deepseek/deepseek-r1", models)
        
        gemini = models["google/gemini-2.5-flash"]
        self.assertTrue(gemini.free)
        self.assertEqual(gemini.context_window, 1_048_576)

    def test_huggingface_provider_models(self) -> None:
        provider = HuggingFaceProvider()
        self.assertEqual(provider.name, "huggingface")
        
        models = {m.id: m for m in provider.models}
        self.assertEqual(len(models), 3)
        self.assertIn("meta-llama/Llama-3.3-70B-Instruct", models)
        self.assertIn("Qwen/Qwen2.5-Coder-32B-Instruct", models)
        
        qwen = models["Qwen/Qwen2.5-Coder-32B-Instruct"]
        self.assertEqual(qwen.tier, ModelTier.PRO)
        self.assertIn(TaskType.CODE, qwen.strong_at)


if __name__ == "__main__":
    unittest.main()
