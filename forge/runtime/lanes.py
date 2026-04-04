from __future__ import annotations

import asyncio
from dataclasses import dataclass
import inspect
import time
from typing import Any, Awaitable, Callable


JobCallable = Callable[[], Awaitable[Any] | Any]


@dataclass(slots=True)
class LaneMetrics:
    lane_id: str
    queued_jobs: int
    active_jobs: int
    processed_jobs: int
    last_duration_ms: float
    last_error: str = ""


@dataclass(slots=True)
class _LaneJob:
    handler: JobCallable
    future: asyncio.Future
    enqueued_at: float


class _LaneWorker:
    def __init__(self, lane_id: str) -> None:
        self.lane_id = lane_id
        self.queue: asyncio.Queue[_LaneJob | None] = asyncio.Queue()
        self.active_jobs = 0
        self.processed_jobs = 0
        self.last_duration_ms = 0.0
        self.last_error = ""
        self._task = asyncio.create_task(self._run(), name=f"forge-lane-{lane_id}")

    async def submit(self, handler: JobCallable) -> Any:
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        await self.queue.put(_LaneJob(handler=handler, future=future, enqueued_at=time.monotonic()))
        return await future

    async def close(self) -> None:
        await self.queue.put(None)
        await self._task

    def metrics(self) -> LaneMetrics:
        return LaneMetrics(
            lane_id=self.lane_id,
            queued_jobs=self.queue.qsize(),
            active_jobs=self.active_jobs,
            processed_jobs=self.processed_jobs,
            last_duration_ms=round(self.last_duration_ms, 2),
            last_error=self.last_error,
        )

    async def _run(self) -> None:
        while True:
            job = await self.queue.get()
            if job is None:
                return

            started = time.monotonic()
            self.active_jobs += 1
            try:
                result = job.handler()
                if inspect.isawaitable(result):
                    result = await result
                self.last_error = ""
                if not job.future.done():
                    job.future.set_result(result)
            except Exception as exc:
                self.last_error = str(exc)
                if not job.future.done():
                    job.future.set_exception(exc)
            finally:
                self.active_jobs = max(0, self.active_jobs - 1)
                self.processed_jobs += 1
                self.last_duration_ms = (time.monotonic() - started) * 1000


class LaneQueueManager:
    """Serial execution per lane, concurrent execution across lanes."""

    def __init__(self) -> None:
        self._lanes: dict[str, _LaneWorker] = {}
        self._lock = asyncio.Lock()

    async def submit(self, lane_id: str, handler: JobCallable) -> Any:
        worker = await self._ensure_lane(lane_id)
        return await worker.submit(handler)

    async def close(self) -> None:
        async with self._lock:
            workers = list(self._lanes.values())
            self._lanes.clear()
        for worker in workers:
            await worker.close()

    def snapshot(self) -> list[dict[str, Any]]:
        return [
            {
                "lane_id": metrics.lane_id,
                "queued_jobs": metrics.queued_jobs,
                "active_jobs": metrics.active_jobs,
                "processed_jobs": metrics.processed_jobs,
                "last_duration_ms": metrics.last_duration_ms,
                "last_error": metrics.last_error,
            }
            for worker in self._lanes.values()
            for metrics in [worker.metrics()]
        ]

    async def _ensure_lane(self, lane_id: str) -> _LaneWorker:
        async with self._lock:
            worker = self._lanes.get(lane_id)
            if worker is None:
                worker = _LaneWorker(lane_id)
                self._lanes[lane_id] = worker
            return worker
