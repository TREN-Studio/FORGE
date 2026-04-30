"""
FORGE Base Provider
====================
The contract every provider must implement.
"""

from __future__ import annotations

import json
import os
import re
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, AsyncIterator, ClassVar

import httpx

from forge.core.models import ForgeResponse, Message, ModelSpec


SECRET_TOKEN_PATTERNS: dict[str, tuple[str, ...]] = {
    "api_key": (
        r"(nvapi-[A-Za-z0-9._-]+)",
        r"(sk-proj-[A-Za-z0-9._-]+)",
        r"(sk-[A-Za-z0-9._-]+)",
        r"(gsk_[A-Za-z0-9._-]+)",
        r"(AIza[0-9A-Za-z_-]{20,})",
        r"(hf_[A-Za-z0-9]{20,})",
    ),
    "global_key": (
        r"([A-Fa-f0-9]{32,64})",
    ),
    "account_id": (
        r"([A-Fa-f0-9]{32})",
    ),
    "email": (
        r"([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})",
    ),
}


def normalize_secret_value(name: str, value: str | None) -> str | None:
    text = (value or "").strip()
    if not text:
        return None

    if name in {"api_key", "global_key"} and text.lower().startswith("bearer "):
        text = text.split(None, 1)[1].strip()

    patterns = SECRET_TOKEN_PATTERNS.get(name, ())
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) <= 1:
        return text

    compact_candidates = [
        line
        for line in lines
        if " " not in line
        and not line.endswith(":")
        and len(line) >= 12
        and not re.fullmatch(r"[A-Za-z][A-Za-z0-9 _-]{0,40}", line)
    ]
    if compact_candidates:
        return compact_candidates[-1]
    return lines[-1]


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
        self._api_key = normalize_secret_value("api_key", api_key) or self._load_key()
        self._model_map: dict[str, ModelSpec] = {model.id: model for model in self.models}

    def _load_key(self) -> str | None:
        direct = normalize_secret_value("api_key", self._config.get("api_key"))
        if direct:
            return direct
        if not self._allow_host_fallback:
            return None
        for env_name in self._candidate_env_names():
            value = normalize_secret_value("api_key", os.environ.get(env_name))
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
            return normalize_secret_value("api_key", keyfile.read_text())
        return None

    def _keydir(self) -> Path:
        return Path.home() / ".forge" / "keys"

    def _load_optional_value(
        self,
        name: str,
        env_names: list[str] | None = None,
    ) -> str | None:
        direct = normalize_secret_value(name, self._config.get(name))
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
            value = normalize_secret_value(name, os.environ.get(candidate))
            if value:
                return value

        sidecar = self._keydir() / f"{self.name}.{name}"
        if sidecar.exists():
            value = normalize_secret_value(name, sidecar.read_text())
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

    async def stream(
        self,
        model: ModelSpec,
        messages: list[Message],
        max_tokens: int = 2048,
        temperature: float = 0.7,
    ) -> AsyncIterator[dict[str, Any]]:
        """Fallback streaming for providers without native streaming support."""
        response = await self.complete(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        if response.content:
            yield {"type": "delta", "delta": response.content}
        yield {"type": "response", "response": response}

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

    async def _stream_openai_compatible(
        self,
        *,
        api_url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        model: ModelSpec,
        provider_name: str | None = None,
        timeout: float = 120.0,
    ) -> AsyncIterator[dict[str, Any]]:
        body = dict(payload)
        body["stream"] = True

        provider = provider_name or self.name
        started = time.monotonic()
        accumulated = ""
        finish_reason = "stop"
        prompt_tokens = 0
        completion_tokens = 0

        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", api_url, json=body, headers=headers) as response:
                if response.status_code == 429:
                    raise RuntimeError(f"quota_exceeded: {provider} rate limit hit")
                if response.status_code != 200:
                    raw = (await response.aread()).decode("utf-8", errors="replace")
                    raise RuntimeError(f"{provider} API error {response.status_code}: {raw[:200]}")

                async for raw_line in response.aiter_lines():
                    line = raw_line.strip()
                    if not line or not line.startswith("data:"):
                        continue
                    chunk = line[5:].strip()
                    if not chunk:
                        continue
                    if chunk == "[DONE]":
                        break
                    try:
                        data = json.loads(chunk)
                    except json.JSONDecodeError:
                        continue

                    usage = data.get("usage") or {}
                    prompt_tokens = int(usage.get("prompt_tokens") or prompt_tokens or 0)
                    completion_tokens = int(usage.get("completion_tokens") or completion_tokens or 0)

                    choices = data.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0] or {}
                    finish_reason = choice.get("finish_reason") or finish_reason

                    delta_payload = choice.get("delta")
                    if delta_payload is None and isinstance(choice.get("message"), dict):
                        delta_payload = choice.get("message")

                    delta_text = self._extract_text_delta(delta_payload)
                    if delta_text:
                        accumulated += delta_text
                        yield {"type": "delta", "delta": delta_text}

        yield {
            "type": "response",
            "response": ForgeResponse(
                content=accumulated,
                model_id=model.id,
                provider=provider,
                latency_ms=(time.monotonic() - started) * 1000,
                input_tokens=prompt_tokens,
                output_tokens=completion_tokens,
                finish_reason=str(finish_reason or "stop"),
            ),
        }

    @staticmethod
    def _extract_text_delta(payload: Any) -> str:
        if payload is None:
            return ""
        if isinstance(payload, str):
            return payload
        if isinstance(payload, list):
            return "".join(BaseProvider._extract_text_delta(item) for item in payload)
        if isinstance(payload, dict):
            for key in ("text", "content", "delta"):
                value = payload.get(key)
                if isinstance(value, str):
                    return value
                if isinstance(value, list):
                    return "".join(BaseProvider._extract_text_delta(item) for item in value)
        return ""

    def __repr__(self) -> str:
        available = "yes" if self.is_available else "no"
        return f"<{self.__class__.__name__} '{self.name}' [{available}] models={len(self.list_models())}>"
