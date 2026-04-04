from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import inspect
import time
from typing import Any, Awaitable, Callable

from forge.runtime.contracts import HeartbeatReport


HeartbeatCallable = Callable[[], Awaitable[dict[str, Any]] | dict[str, Any]]


@dataclass(slots=True)
class ScheduledTask:
    name: str
    interval_seconds: int
    handler: HeartbeatCallable
    run_immediately: bool = False
    last_started_at: float = 0.0
    last_completed_at: float = 0.0
    last_status: str = "idle"
    last_error: str = ""
    last_report: dict[str, Any] = field(default_factory=dict)

    def due(self, now: float) -> bool:
        if self.last_started_at == 0.0:
            return self.run_immediately
        return (now - self.last_started_at) >= self.interval_seconds


class HeartbeatDaemon:
    """Simple recurring daemon for autonomous maintenance tasks."""

    def __init__(self, poll_interval_seconds: int = 5) -> None:
        self.poll_interval_seconds = poll_interval_seconds
        self._tasks: dict[str, ScheduledTask] = {}
        self._runner: asyncio.Task | None = None
        self._shutdown = asyncio.Event()

    def register(self, task: ScheduledTask) -> None:
        self._tasks[task.name] = task

    async def start(self) -> None:
        if self._runner is not None and not self._runner.done():
            return
        self._shutdown.clear()
        self._runner = asyncio.create_task(self._run_loop(), name="forge-heartbeat")

    async def stop(self) -> None:
        self._shutdown.set()
        if self._runner is not None:
            await self._runner
            self._runner = None

    async def tick_once(self) -> list[HeartbeatReport]:
        reports: list[HeartbeatReport] = []
        now = time.monotonic()
        for task in self._tasks.values():
            if task.last_started_at == 0.0 or task.due(now):
                reports.append(await self._run_task(task))
        return reports

    def snapshot(self) -> dict[str, Any]:
        return {
            name: {
                "interval_seconds": task.interval_seconds,
                "last_status": task.last_status,
                "last_error": task.last_error,
                "last_completed_at": task.last_completed_at,
                "last_report": task.last_report,
            }
            for name, task in self._tasks.items()
        }

    async def _run_loop(self) -> None:
        while not self._shutdown.is_set():
            await self.tick_once()
            try:
                await asyncio.wait_for(self._shutdown.wait(), timeout=self.poll_interval_seconds)
            except asyncio.TimeoutError:
                continue

    async def _run_task(self, task: ScheduledTask) -> HeartbeatReport:
        task.last_started_at = time.monotonic()
        started = time.monotonic()
        try:
            payload = task.handler()
            if inspect.isawaitable(payload):
                payload = await payload
            task.last_status = "completed"
            task.last_error = ""
            task.last_report = payload or {}
            status = "completed"
        except Exception as exc:
            task.last_status = "failed"
            task.last_error = str(exc)
            task.last_report = {}
            payload = {"error": str(exc)}
            status = "failed"
        task.last_completed_at = time.monotonic()
        return HeartbeatReport(
            task_name=task.name,
            status=status,
            duration_ms=round((time.monotonic() - started) * 1000, 2),
            details=payload,
        )
