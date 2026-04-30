"""
FORGE Session
==============
The unified entry point. One object that ties everything together:
router + quota guardian + discovery + memory + all providers.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from queue import Queue
import threading
from typing import Any

from forge.core.discovery import SelfDiscoveryEngine
from forge.core.models import ForgeResponse, Message, TaskType
from forge.core.quota import QuotaGuardian
from forge.core.router import ForgeRouter
from forge.memory.graph import MemoryGraph
from forge.providers import iter_provider_classes

logger = logging.getLogger("forge.session")


def _load_providers(
    router: ForgeRouter,
    guardian: QuotaGuardian,
    provider_secrets: dict[str, dict[str, str]] | None = None,
    allow_host_fallback: bool = True,
) -> None:
    """Auto-register all built-in providers that are usable right now."""
    provider_secrets = provider_secrets or {}
    for cls in iter_provider_classes():
        try:
            provider_name = getattr(cls, "name", cls.__name__.replace("Provider", "").lower())
            secrets = provider_secrets.get(provider_name, {})
            provider = cls(
                api_key=secrets.get("api_key"),
                config=secrets,
                allow_host_fallback=allow_host_fallback,
            )
            if provider.is_available or cls.__name__ == "OllamaProvider":
                router.register(provider)
                guardian.register_provider(provider.name)
                logger.info("provider_online %s", provider.name)
        except Exception as exc:
            logger.debug("provider_skip %s: %s", cls.__name__, exc)


class ForgeSession:
    """
    One session = one complete FORGE environment.
    Manages conversation state, memory, routing, and discovery.

    Synchronous wrapper around the async core so normal
    Python scripts work without `await`.
    """

    def __init__(
        self,
        system_prompt: str | None = None,
        memory: bool = True,
        db_path: Path | None = None,
        provider_secrets: dict[str, dict[str, str]] | None = None,
        allow_host_fallback: bool = True,
    ) -> None:
        self._router = ForgeRouter()
        self._guardian = QuotaGuardian(self._router)
        self._discovery = SelfDiscoveryEngine(self._router)
        self._memory = MemoryGraph(db_path) if memory else None
        self._system = system_prompt or self._default_system()
        self._conv_id: str | None = None
        self._history: list[Message] = []
        self._provider_secrets = provider_secrets or {}
        self._allow_host_fallback = allow_host_fallback
        self._last_response: ForgeResponse | None = None

        try:
            self._loop = asyncio.get_event_loop()
            if self._loop.is_closed():
                raise RuntimeError
        except RuntimeError:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)

        logger.info("FORGE booting - registering providers...")
        _load_providers(
            self._router,
            self._guardian,
            self._provider_secrets,
            allow_host_fallback=self._allow_host_fallback,
        )
        logger.info("FORGE ready with %s live models", self._router.status()["models_online"])

    def ask(
        self,
        prompt: str,
        task_type: str | TaskType = TaskType.GENERAL,
        max_tokens: int = 2048,
        temperature: float = 0.7,
        remember: bool = True,
    ) -> str:
        """Send a message. Returns the response text."""
        return self.ask_response(
            prompt,
            task_type=task_type,
            max_tokens=max_tokens,
            temperature=temperature,
            remember=remember,
        ).content

    def ask_response(
        self,
        prompt: str,
        task_type: str | TaskType = TaskType.GENERAL,
        max_tokens: int = 2048,
        temperature: float = 0.7,
        remember: bool = True,
    ) -> ForgeResponse:
        """Send a message and return the full provider response."""
        return self._loop.run_until_complete(
            self._ask_response_async(prompt, task_type, max_tokens, temperature, remember)
        )

    def stream_response(
        self,
        prompt: str,
        task_type: str | TaskType = TaskType.GENERAL,
        max_tokens: int = 2048,
        temperature: float = 0.7,
        remember: bool = True,
    ):
        sentinel = object()
        queue: Queue[object] = Queue()

        def runner() -> None:
            async def produce() -> None:
                try:
                    async for event in self._stream_response_async(
                        prompt,
                        task_type,
                        max_tokens,
                        temperature,
                        remember,
                    ):
                        queue.put(event)
                except Exception as exc:
                    queue.put({"type": "error", "error": str(exc)})
                finally:
                    queue.put(sentinel)

            asyncio.run(produce())

        threading.Thread(target=runner, daemon=True).start()
        while True:
            item = queue.get()
            if item is sentinel:
                break
            if isinstance(item, dict):
                yield item

    def reset(self) -> None:
        """Clear conversation history while keeping the persistent graph."""
        self._history.clear()
        self._conv_id = None

    def leaderboard(self, task_type: str = "general") -> list[dict]:
        """Show current model rankings."""
        return self._router.leaderboard(TaskType(task_type))

    def quota_health(self) -> dict:
        """Show quota status for all providers."""
        return self._guardian.get_health()

    def memory_stats(self) -> dict:
        return self._memory.stats() if self._memory else {}

    def discover_models(self) -> dict:
        """Run discovery now and attach compatible models to live providers."""
        return self._loop.run_until_complete(self._discover_models_async())

    async def _ask_async(
        self,
        prompt: str,
        task_type: str | TaskType,
        max_tokens: int,
        temperature: float,
        remember: bool,
    ) -> str:
        response = await self._ask_response_async(
            prompt,
            task_type,
            max_tokens,
            temperature,
            remember,
        )
        return response.content

    async def _ask_response_async(
        self,
        prompt: str,
        task_type: str | TaskType,
        max_tokens: int,
        temperature: float,
        remember: bool,
    ) -> ForgeResponse:
        task_type = self._normalize_task_type(task_type)
        messages = self._build_messages(prompt)

        response: ForgeResponse = await self._router.route(
            messages=messages,
            task_type=task_type,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        self._remember_response(prompt, response, remember=remember)
        return response

    async def _stream_response_async(
        self,
        prompt: str,
        task_type: str | TaskType,
        max_tokens: int,
        temperature: float,
        remember: bool,
    ):
        normalized_task_type = self._normalize_task_type(task_type)
        messages = self._build_messages(prompt)

        response: ForgeResponse | None = None
        async for event in self._router.route_stream(
            messages=messages,
            task_type=normalized_task_type,
            max_tokens=max_tokens,
            temperature=temperature,
        ):
            kind = str(event.get("type") or "").strip().lower()
            if kind in {"start", "delta"}:
                yield event
                continue
            if kind == "response":
                maybe_response = event.get("response")
                if isinstance(maybe_response, ForgeResponse):
                    response = maybe_response
                    self._remember_response(prompt, response, remember=remember)
                    yield event

        if response is None:
            raise RuntimeError("Streaming finished without a final response.")

    @property
    def last_response(self) -> ForgeResponse | None:
        return self._last_response

    async def _discover_models_async(self) -> dict:
        return await self._discovery.run_once()

    def _normalize_task_type(self, task_type: str | TaskType) -> TaskType:
        if isinstance(task_type, str):
            try:
                return TaskType(task_type)
            except ValueError:
                return TaskType.GENERAL
        return task_type

    def _build_messages(self, prompt: str) -> list[Message]:
        if self._conv_id is None and self._memory:
            self._conv_id = self._memory.new_conversation()

        system = self._system
        if self._memory:
            mem_ctx = self._memory.recall_all(limit=20)
            if mem_ctx:
                system = f"{system}\n\n{mem_ctx}"

        messages: list[Message] = [Message(role="system", content=system)]
        messages.extend(self._history[-20:])
        messages.append(Message(role="user", content=prompt))
        return messages

    def _remember_response(self, prompt: str, response: ForgeResponse, *, remember: bool) -> None:
        self._last_response = response
        self._guardian.record_usage(response.provider, response.total_tokens)
        self._history.append(Message(role="user", content=prompt))
        self._history.append(Message(role="assistant", content=response.content))

        if self._memory and self._conv_id and remember:
            self._memory.log_message(self._conv_id, "user", prompt)
            self._memory.log_message(
                self._conv_id,
                "assistant",
                response.content,
                model_used=response.model_id,
                provider_used=response.provider,
                latency_ms=response.latency_ms,
                tokens=response.total_tokens,
            )

    @staticmethod
    def _default_system() -> str:
        return (
            "You are FORGE - an expert AI agent. "
            "You are precise, direct, and deeply capable. "
            "When writing code, write production-quality code with no placeholders. "
            "When answering questions, be thorough but concise. "
            "You have access to tools and can execute code when needed."
        )
