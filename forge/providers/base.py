"""
FORGE Base Provider
====================
The contract every provider must implement.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import ClassVar

from forge.core.models import ForgeResponse, Message, ModelSpec


class BaseProvider(ABC):
    """
    Abstract base for all FORGE providers.

    Every provider exposes:
      - name         : unique provider identifier
      - models       : initial list of ModelSpec values
      - is_available : True if provider can accept calls right now
      - complete()   : the actual API call

    Providers stay stateless. Runtime state lives in the router and scores.
    """

    name: ClassVar[str]
    daily_token_limit: int = 0
    daily_request_limit: int = 0

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or self._load_key()
        self._model_map: dict[str, ModelSpec] = {model.id: model for model in self.models}

    def _load_key(self) -> str | None:
        for env_name in self._candidate_env_names():
            value = os.environ.get(env_name)
            if value:
                return value
        return self._read_keyfile()

    def _candidate_env_names(self) -> list[str]:
        upper = self.name.upper()
        alias_map = {
            "gemini": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
            "groq": ["GROQ_API_KEY"],
            "deepseek": ["DEEPSEEK_API_KEY"],
            "openrouter": ["OPENROUTER_API_KEY"],
            "ollama": ["OLLAMA_API_KEY"],
        }
        names = [f"FORGE_{upper}_KEY", f"{upper}_API_KEY", f"{upper}_KEY"]
        names.extend(alias_map.get(self.name, []))
        return list(dict.fromkeys(names))

    def _read_keyfile(self) -> str | None:
        from pathlib import Path

        keyfile = Path.home() / ".forge" / "keys" / self.name
        if keyfile.exists():
            return keyfile.read_text().strip()
        return None

    @property
    @abstractmethod
    def models(self) -> list[ModelSpec]:
        """Return the built-in models offered by this provider."""
        ...

    @abstractmethod
    async def complete(
        self,
        model: ModelSpec,
        messages: list[Message],
        max_tokens: int = 2048,
        temperature: float = 0.7,
    ) -> ForgeResponse:
        """Call the model and return a ForgeResponse."""
        ...

    @property
    def is_available(self) -> bool:
        return True

    def get_model(self, model_id: str) -> ModelSpec | None:
        return self._model_map.get(model_id)

    def list_models(self) -> list[ModelSpec]:
        return list(self._model_map.values())

    def add_models(self, models: list[ModelSpec]) -> int:
        added = 0
        for spec in models:
            if spec.provider != self.name:
                continue
            if spec.id in self._model_map:
                continue
            self._model_map[spec.id] = spec
            added += 1
        return added

    @property
    def api_key(self) -> str | None:
        return self._api_key

    def __repr__(self) -> str:
        available = "yes" if self.is_available else "no"
        return f"<{self.__class__.__name__} '{self.name}' [{available}] models={len(self.list_models())}>"
