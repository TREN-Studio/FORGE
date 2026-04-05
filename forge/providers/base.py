"""
FORGE Base Provider
====================
The contract every provider must implement.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from pathlib import Path
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

    def __init__(
        self,
        api_key: str | None = None,
        config: dict[str, str] | None = None,
        allow_host_fallback: bool = True,
    ) -> None:
        self._config = config or {}
        self._allow_host_fallback = allow_host_fallback
        self._api_key = api_key or self._load_key()
        self._model_map: dict[str, ModelSpec] = {model.id: model for model in self.models}

    def _load_key(self) -> str | None:
        direct = self._config.get("api_key")
        if direct:
            return direct
        if not self._allow_host_fallback:
            return None
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
            "openai": ["OPENAI_API_KEY"],
            "anthropic": ["ANTHROPIC_API_KEY"],
            "mistral": ["MISTRAL_API_KEY"],
            "together": ["TOGETHER_API_KEY"],
            "nvidia": ["NVIDIA_API_KEY", "NGC_API_KEY"],
            "cloudflare": ["CLOUDFLARE_API_TOKEN", "CLOUDFLARE_API_KEY"],
        }
        names = [f"FORGE_{upper}_KEY", f"{upper}_API_KEY", f"{upper}_KEY"]
        names.extend(alias_map.get(self.name, []))
        return list(dict.fromkeys(names))

    def _read_keyfile(self) -> str | None:
        keyfile = self._keydir() / self.name
        if keyfile.exists():
            return keyfile.read_text().strip()
        return None

    def _keydir(self) -> Path:
        return Path.home() / ".forge" / "keys"

    def _load_optional_value(
        self,
        name: str,
        env_names: list[str] | None = None,
    ) -> str | None:
        direct = self._config.get(name)
        if direct:
            return direct
        if not self._allow_host_fallback:
            return None
        upper_provider = self.name.upper()
        upper_name = name.upper()
        candidates = [
            f"FORGE_{upper_provider}_{upper_name}",
            f"{upper_provider}_{upper_name}",
        ]
        if env_names:
            candidates.extend(env_names)
        for candidate in dict.fromkeys(candidates):
            value = os.environ.get(candidate)
            if value:
                return value

        sidecar = self._keydir() / f"{self.name}.{name}"
        if sidecar.exists():
            value = sidecar.read_text().strip()
            return value or None
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
