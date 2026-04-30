"""
FORGE x Cloudflare Workers AI Provider
======================================
Workers AI using Cloudflare's OpenAI-compatible chat completions API.
"""

from __future__ import annotations

import time
from typing import ClassVar

import httpx

from forge.core.models import ForgeResponse, Message, ModelSpec, ModelTier, TaskType
from forge.providers.base import BaseProvider


class CloudflareProvider(BaseProvider):
    name: ClassVar[str] = "cloudflare"
    daily_token_limit: int = 0
    daily_request_limit: int = 0

    def __init__(
        self,
        api_key: str | None = None,
        config: dict[str, str] | None = None,
        allow_host_fallback: bool = True,
    ) -> None:
        super().__init__(api_key=api_key, config=config, allow_host_fallback=allow_host_fallback)
        self._account_id = self._load_optional_value(
            "account_id",
            ["CLOUDFLARE_ACCOUNT_ID", "CF_ACCOUNT_ID"],
        )
        self._global_key = self._load_optional_value(
            "global_key",
            ["CLOUDFLARE_GLOBAL_API_KEY", "CLOUDFLARE_API_KEY"],
        )
        self._email = self._load_optional_value(
            "email",
            ["CLOUDFLARE_EMAIL", "CF_EMAIL"],
        )

    @property
    def models(self) -> list[ModelSpec]:
        return [
            ModelSpec(
                id="@cf/meta/llama-3.1-8b-instruct",
                provider="cloudflare",
                display_name="Workers AI Llama 3.1 8B Instruct",
                tier=ModelTier.BASE,
                context_window=7_968,
                max_output_tokens=4_096,
                strong_at=[TaskType.GENERAL, TaskType.FAST],
                tags=["edge", "fast", "instruct"],
            ),
            ModelSpec(
                id="@cf/zai-org/glm-4.7-flash",
                provider="cloudflare",
                display_name="Workers AI GLM 4.7 Flash",
                tier=ModelTier.PRO,
                context_window=131_072,
                max_output_tokens=8_192,
                supports_tools=True,
                strong_at=[TaskType.RESEARCH, TaskType.GENERAL, TaskType.CREATIVE],
                tags=["large-context", "edge", "multilingual"],
            ),
            ModelSpec(
                id="@cf/openai/gpt-oss-120b",
                provider="cloudflare",
                display_name="Workers AI GPT-OSS 120B",
                tier=ModelTier.ULTRA,
                context_window=131_072,
                max_output_tokens=8_192,
                supports_tools=True,
                strong_at=[TaskType.CODE, TaskType.REASONING, TaskType.GENERAL],
                tags=["reasoning", "coding", "edge"],
            ),
        ]

    @property
    def is_available(self) -> bool:
        return bool(self._account_id and (self._api_key or (self._global_key and self._email)))

    async def complete(
        self,
        model: ModelSpec,
        messages: list[Message],
        max_tokens: int = 2048,
        temperature: float = 0.7,
    ) -> ForgeResponse:
        if not self._account_id:
            raise RuntimeError("Cloudflare account ID is missing")

        api_url = (
            f"https://api.cloudflare.com/client/v4/accounts/{self._account_id}"
            "/ai/v1/chat/completions"
        )
        payload = {
            "model": model.id,
            "messages": [message.model_dump(exclude_none=True) for message in messages],
            "max_tokens": min(max_tokens, model.max_output_tokens),
            "temperature": temperature,
        }
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        elif self._global_key and self._email:
            headers["X-Auth-Key"] = self._global_key
            headers["X-Auth-Email"] = self._email
        else:
            raise RuntimeError("Cloudflare credentials are missing")

        started = time.monotonic()
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.post(api_url, json=payload, headers=headers)

        if resp.status_code == 429:
            raise RuntimeError("quota_exceeded: Cloudflare Workers AI rate limit hit")
        if resp.status_code != 200:
            raise RuntimeError(f"Cloudflare API error {resp.status_code}: {resp.text[:200]}")

        data = resp.json()
        latency = (time.monotonic() - started) * 1000
        choice = data["choices"][0]
        usage = data.get("usage", {})

        return ForgeResponse(
            content=choice["message"].get("content", "") or "",
            model_id=model.id,
            provider="cloudflare",
            latency_ms=latency,
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            finish_reason=choice.get("finish_reason", "stop"),
        )

    async def stream(
        self,
        model: ModelSpec,
        messages: list[Message],
        max_tokens: int = 2048,
        temperature: float = 0.7,
    ):
        if not self._account_id:
            raise RuntimeError("Cloudflare account ID is missing")
        api_url = (
            f"https://api.cloudflare.com/client/v4/accounts/{self._account_id}"
            "/ai/v1/chat/completions"
        )
        payload = {
            "model": model.id,
            "messages": [message.model_dump(exclude_none=True) for message in messages],
            "max_tokens": min(max_tokens, model.max_output_tokens),
            "temperature": temperature,
        }
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        elif self._global_key and self._email:
            headers["X-Auth-Key"] = self._global_key
            headers["X-Auth-Email"] = self._email
        else:
            raise RuntimeError("Cloudflare credentials are missing")

        async for event in self._stream_openai_compatible(
            api_url=api_url,
            payload=payload,
            headers=headers,
            model=model,
            provider_name="cloudflare",
            timeout=90,
        ):
            yield event
