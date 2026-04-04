from __future__ import annotations

import asyncio
import inspect
import threading
from concurrent.futures import Future
from typing import Any, Callable

from forge.runtime.lanes import LaneQueueManager


JobCallable = Callable[[], Any]

DEFAULT_SERVICE_LANES: dict[str, int] = {
    "council:action": 2,
    "council:research": 2,
    "council:critic": 1,
}


class DistributedCouncilRuntime:
    """Background council runtime with service-aware lane balancing and backpressure."""

    def __init__(
        self,
        *,
        service_lanes: dict[str, int] | None = None,
        max_queue_per_lane: int = 4,
    ) -> None:
        self._started = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lanes: LaneQueueManager | None = None
        self._service_lanes = {
            service: [f"{service}:{index}" for index in range(max(1, count))]
            for service, count in (service_lanes or DEFAULT_SERVICE_LANES).items()
        }
        self._max_queue_per_lane = max_queue_per_lane
        self._thread = threading.Thread(target=self._bootstrap, name="forge-council-runtime", daemon=True)
        self._thread.start()
        self._started.wait(timeout=10)
        if self._loop is None or self._lanes is None:
            raise RuntimeError("DistributedCouncilRuntime failed to start.")

    def submit(self, service_name: str, handler: JobCallable) -> Any:
        return self.submit_future(service_name, handler).result()

    def submit_future(self, service_name: str, handler: JobCallable) -> Future:
        return asyncio.run_coroutine_threadsafe(self._submit(service_name, handler), self._loop)

    def snapshot(self) -> dict[str, Any]:
        future = asyncio.run_coroutine_threadsafe(self._snapshot(), self._loop)
        return future.result()

    def close(self) -> None:
        if self._loop is None or self._lanes is None:
            return
        future = asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop)
        future.result(timeout=10)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=10)

    def _bootstrap(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._lanes = LaneQueueManager()
        self._started.set()
        loop.run_forever()
        loop.run_until_complete(self._lanes.close())
        loop.close()

    async def _submit(self, service_name: str, handler: JobCallable) -> Any:
        lane_id = await self._choose_lane(service_name)

        async def lane_job() -> Any:
            return await self._call(handler)

        return await self._lanes.submit(lane_id, lane_job)

    async def _choose_lane(self, service_name: str) -> str:
        lane_ids = self._service_lanes.get(service_name, [service_name])
        snapshot = self._lane_metrics_map()

        candidates: list[tuple[int, int, str]] = []
        for lane_id in lane_ids:
            metrics = snapshot.get(
                lane_id,
                {
                    "queued_jobs": 0,
                    "active_jobs": 0,
                    "processed_jobs": 0,
                    "last_error": "",
                },
            )
            queue_score = int(metrics.get("queued_jobs", 0)) + int(metrics.get("active_jobs", 0))
            processed = int(metrics.get("processed_jobs", 0))
            candidates.append((queue_score, processed, lane_id))

        candidates.sort(key=lambda item: (item[0], item[1], item[2]))
        best_score, _, best_lane = candidates[0]
        if best_score > self._max_queue_per_lane:
            raise RuntimeError(
                f"Worker service `{service_name}` is under backpressure; every lane is above queue threshold."
            )
        return best_lane

    async def _snapshot(self) -> dict[str, Any]:
        lane_snapshot = self._lanes.snapshot()
        lane_map = {lane["lane_id"]: lane for lane in lane_snapshot}
        services: list[dict[str, Any]] = []
        for service_name, lane_ids in self._service_lanes.items():
            workers = [lane_map.get(lane_id, self._empty_lane(lane_id)) for lane_id in lane_ids]
            services.append(
                {
                    "service": service_name,
                    "status": self._service_status(workers, self._max_queue_per_lane),
                    "queued_jobs": sum(int(worker.get("queued_jobs", 0)) for worker in workers),
                    "active_jobs": sum(int(worker.get("active_jobs", 0)) for worker in workers),
                    "workers": workers,
                }
            )
        return {"services": services, "lanes": lane_snapshot}

    async def _shutdown(self) -> None:
        await self._lanes.close()

    def _lane_metrics_map(self) -> dict[str, dict[str, Any]]:
        return {lane["lane_id"]: lane for lane in self._lanes.snapshot()}

    @staticmethod
    def _service_status(workers: list[dict[str, Any]], max_queue_per_lane: int) -> str:
        if any(worker.get("last_error") for worker in workers):
            return "failed"
        if any((int(worker.get("queued_jobs", 0)) + int(worker.get("active_jobs", 0))) > max_queue_per_lane for worker in workers):
            return "overloaded"
        if any(int(worker.get("active_jobs", 0)) > 0 for worker in workers):
            return "busy"
        return "idle"

    @staticmethod
    def _empty_lane(lane_id: str) -> dict[str, Any]:
        return {
            "lane_id": lane_id,
            "queued_jobs": 0,
            "active_jobs": 0,
            "processed_jobs": 0,
            "last_duration_ms": 0.0,
            "last_error": "",
        }

    @staticmethod
    async def _call(handler: JobCallable) -> Any:
        if inspect.iscoroutinefunction(handler):
            return await handler()
        result = await asyncio.to_thread(handler)
        if inspect.isawaitable(result):
            return await result
        return result
