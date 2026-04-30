from __future__ import annotations

import asyncio
import ctypes
import inspect
import os
import threading
import time
from concurrent.futures import Future
from typing import Any, Callable

from aiohttp import ClientSession, ClientTimeout

from forge.brain.worker_executor import WorkerTaskExecutor
from forge.brain.worker_protocol import WorkerHeartbeat, WorkerRegistration, WorkerTask, WorkerTaskResult
from forge.runtime.lanes import LaneQueueManager
from forge.runtime.state_store import PersistentStateStore

try:
    import resource
except ImportError:  # pragma: no cover - Windows
    resource = None


JobCallable = Callable[[], Any]

DEFAULT_SERVICE_LANES: dict[str, int] = {
    "council:action": 2,
    "council:research": 2,
    "council:critic": 1,
}


def _memory_usage_mb() -> float:
    if os.name == "nt":
        class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
        process = ctypes.windll.kernel32.GetCurrentProcess()
        ok = ctypes.windll.psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb)
        if ok:
            return round(counters.WorkingSetSize / (1024 * 1024), 2)
        return 0.0

    if resource is None:
        return 0.0
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if hasattr(os, "uname") and os.uname().sysname.lower() == "darwin":
        return round(usage / (1024 * 1024), 2)
    return round(usage / 1024, 2)


class DistributedCouncilRuntime:
    """Hybrid council runtime: local lanes plus gateway-registered worker services."""

    def __init__(
        self,
        *,
        state_store: PersistentStateStore,
        service_lanes: dict[str, int] | None = None,
        max_queue_per_lane: int = 4,
        remote_request_timeout_seconds: int = 90,
    ) -> None:
        self._state_store = state_store
        self._started = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lanes: LaneQueueManager | None = None
        self._executor = WorkerTaskExecutor()
        self._service_lanes = {
            service: [f"{service}:{index}" for index in range(max(1, count))]
            for service, count in (service_lanes or DEFAULT_SERVICE_LANES).items()
        }
        self._max_queue_per_lane = max_queue_per_lane
        self._remote_request_timeout_seconds = remote_request_timeout_seconds
        self._thread = threading.Thread(target=self._bootstrap, name="forge-council-runtime", daemon=True)
        self._thread.start()
        self._started.wait(timeout=10)
        if self._loop is None or self._lanes is None:
            raise RuntimeError("DistributedCouncilRuntime failed to start.")

    def submit(self, service_name: str, handler: JobCallable) -> Any:
        return self.submit_future(service_name, handler).result()

    def submit_future(self, service_name: str, handler: JobCallable) -> Future:
        return asyncio.run_coroutine_threadsafe(self._submit_callable(service_name, handler), self._loop)

    def submit_task(self, task: WorkerTask) -> Any:
        return self.submit_task_future(task).result()

    def submit_task_future(self, task: WorkerTask) -> Future:
        return asyncio.run_coroutine_threadsafe(self._submit_task(task), self._loop)

    def register_remote_worker(self, registration: WorkerRegistration) -> None:
        self._state_store.register_worker(
            worker_id=registration.worker_id,
            endpoint_url=registration.endpoint_url,
            services=registration.services,
            capabilities=registration.capabilities,
            process_mode=registration.process_mode,
            lease_ttl_seconds=registration.lease_ttl_seconds,
            metadata=registration.metadata,
        )

    def heartbeat_worker(self, heartbeat: WorkerHeartbeat) -> None:
        self._state_store.heartbeat_worker(
            worker_id=heartbeat.worker_id,
            status=heartbeat.status,
            metrics=heartbeat.metrics,
            lease_ttl_seconds=heartbeat.lease_ttl_seconds,
        )

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

    async def _submit_callable(self, service_name: str, handler: JobCallable) -> Any:
        lane_id = await self._choose_local_lane(service_name)

        async def lane_job() -> Any:
            if inspect.iscoroutinefunction(handler):
                return await handler()
            result = await asyncio.to_thread(handler)
            if inspect.isawaitable(result):
                return await result
            return result

        return await self._lanes.submit(lane_id, lane_job)

    async def _submit_task(self, task: WorkerTask) -> Any:
        if task.remote_allowed:
            remote_workers = self._available_remote_workers(task.service_name)
            for worker in remote_workers:
                try:
                    return await self._submit_remote(task, worker)
                except Exception:
                    continue
        return await self._submit_local(task)

    async def _submit_local(self, task: WorkerTask) -> Any:
        local_worker_id = "local-process"
        claim = self._state_store.claim_task(
            idempotency_key=task.idempotency_key,
            worker_id=local_worker_id,
            service_name=task.service_name,
            operation=task.operation,
            mission_id=task.mission_id,
            step_id=task.step_id,
            lease_ttl_seconds=task.lease_ttl_seconds,
        )
        if claim.status == "cached":
            return claim.cached_result
        if claim.status == "busy":
            raise RuntimeError(
                f"Task `{task.idempotency_key}` is already leased by `{claim.worker_id}` until {claim.lease_expires_at}."
            )

        self._state_store.mark_task_running(idempotency_key=task.idempotency_key, ticket_id=claim.ticket_id)
        lane_id = await self._choose_local_lane(task.service_name)

        async def lane_job() -> Any:
            result = await asyncio.to_thread(self._executor.execute, task, worker_id=local_worker_id)
            return result.output

        try:
            output = await self._lanes.submit(lane_id, lane_job)
            self._state_store.complete_task(
                idempotency_key=task.idempotency_key,
                ticket_id=claim.ticket_id,
                result=output,
            )
            return output
        except Exception as exc:
            self._state_store.fail_task(
                idempotency_key=task.idempotency_key,
                ticket_id=claim.ticket_id,
                error=str(exc),
                release=True,
            )
            raise

    async def _submit_remote(self, task: WorkerTask, worker: dict[str, Any]) -> Any:
        worker_id = worker["worker_id"]
        claim = self._state_store.claim_task(
            idempotency_key=task.idempotency_key,
            worker_id=worker_id,
            service_name=task.service_name,
            operation=task.operation,
            mission_id=task.mission_id,
            step_id=task.step_id,
            lease_ttl_seconds=task.lease_ttl_seconds,
        )
        if claim.status == "cached":
            return claim.cached_result
        if claim.status == "busy":
            raise RuntimeError(
                f"Task `{task.idempotency_key}` already leased by `{claim.worker_id}` until {claim.lease_expires_at}."
            )

        self._state_store.mark_task_running(idempotency_key=task.idempotency_key, ticket_id=claim.ticket_id)
        timeout = ClientTimeout(total=max(5, task.timeout_seconds))
        endpoint = worker["endpoint_url"].rstrip("/") + "/api/worker/execute"

        try:
            async with ClientSession(timeout=timeout) as session:
                async with session.post(endpoint, json=task.model_dump(mode="json")) as response:
                    payload = await response.json()
                    if response.status >= 400:
                        raise RuntimeError(payload.get("error", f"Remote worker `{worker_id}` failed."))
            result = WorkerTaskResult.model_validate(payload)
            self._state_store.complete_task(
                idempotency_key=task.idempotency_key,
                ticket_id=claim.ticket_id,
                result=result.output,
            )
            return result.output
        except Exception as exc:
            self._state_store.fail_task(
                idempotency_key=task.idempotency_key,
                ticket_id=claim.ticket_id,
                error=str(exc),
                release=True,
            )
            raise

    async def _choose_local_lane(self, service_name: str) -> str:
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
                },
            )
            queue_score = int(metrics.get("queued_jobs", 0)) + int(metrics.get("active_jobs", 0))
            processed = int(metrics.get("processed_jobs", 0))
            candidates.append((queue_score, processed, lane_id))
        candidates.sort(key=lambda item: (item[0], item[1], item[2]))
        best_score, _, best_lane = candidates[0]
        if best_score > self._max_queue_per_lane:
            raise RuntimeError(
                f"Worker service `{service_name}` is under backpressure; every local lane is above queue threshold."
            )
        return best_lane

    def _available_remote_workers(self, service_name: str) -> list[dict[str, Any]]:
        candidates = [
            worker
            for worker in self._state_store.list_workers()
            if not worker.get("is_stale") and service_name in worker.get("services", [])
        ]
        candidates.sort(
            key=lambda worker: (
                int(worker.get("metrics", {}).get("queue_length", 0)),
                int(worker.get("metrics", {}).get("active_jobs", 0)),
                float(worker.get("metrics", {}).get("avg_processing_ms", 0.0)),
                worker["worker_id"],
            )
        )
        return candidates

    async def _snapshot(self) -> dict[str, Any]:
        lane_snapshot = self._lanes.snapshot()
        lane_map = {lane["lane_id"]: lane for lane in lane_snapshot}
        remote_workers = self._state_store.list_workers()
        services: list[dict[str, Any]] = []
        for service_name, lane_ids in self._service_lanes.items():
            local_workers = [self._local_worker_view(lane_map.get(lane_id, self._empty_lane(lane_id))) for lane_id in lane_ids]
            attached_remote = [
                self._remote_worker_view(worker)
                for worker in remote_workers
                if service_name in worker.get("services", [])
            ]
            workers = local_workers + attached_remote
            services.append(
                {
                    "service": service_name,
                    "status": self._service_status(workers, self._max_queue_per_lane),
                    "queued_jobs": sum(int(worker.get("queued_jobs", 0)) for worker in workers),
                    "active_jobs": sum(int(worker.get("active_jobs", 0)) for worker in workers),
                    "queue_length": sum(int(worker.get("queue_length", 0)) for worker in workers),
                    "avg_processing_ms": round(
                        (
                            sum(float(worker.get("avg_processing_ms", 0.0)) for worker in workers if int(worker.get("processed_jobs", 0)) > 0)
                            / max(1, len([worker for worker in workers if int(worker.get("processed_jobs", 0)) > 0]))
                        ),
                        2,
                    ) if workers else 0.0,
                    "workers": workers,
                }
            )
        return {"services": services, "lanes": lane_snapshot, "registered_workers": remote_workers}

    async def _shutdown(self) -> None:
        await self._lanes.close()

    def _lane_metrics_map(self) -> dict[str, dict[str, Any]]:
        return {lane["lane_id"]: lane for lane in self._lanes.snapshot()}

    @staticmethod
    def _service_status(workers: list[dict[str, Any]], max_queue_per_lane: int) -> str:
        if any(worker.get("last_error") or worker.get("status") == "failed" for worker in workers):
            return "failed"
        if any(int(worker.get("queue_length", 0)) > max_queue_per_lane for worker in workers):
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
            "avg_processing_ms": 0.0,
            "queue_length": 0,
            "last_error": "",
        }

    @staticmethod
    def _local_worker_view(lane: dict[str, Any]) -> dict[str, Any]:
        return {
            "worker_id": f"local::{lane['lane_id']}",
            "lane_id": lane["lane_id"],
            "location": "local",
            "process_mode": "in_process",
            "status": "failed" if lane.get("last_error") else ("busy" if int(lane.get("active_jobs", 0)) > 0 else "idle"),
            "queued_jobs": int(lane.get("queued_jobs", 0)),
            "active_jobs": int(lane.get("active_jobs", 0)),
            "queue_length": int(lane.get("queue_length", 0)),
            "processed_jobs": int(lane.get("processed_jobs", 0)),
            "avg_processing_ms": float(lane.get("avg_processing_ms", 0.0)),
            "last_duration_ms": float(lane.get("last_duration_ms", 0.0)),
            "mem_usage_mb": _memory_usage_mb(),
            "last_error": lane.get("last_error", ""),
        }

    @staticmethod
    def _remote_worker_view(worker: dict[str, Any]) -> dict[str, Any]:
        metrics = worker.get("metrics", {})
        return {
            "worker_id": worker["worker_id"],
            "lane_id": worker["worker_id"],
            "location": worker.get("endpoint_url", ""),
            "process_mode": worker.get("process_mode", "process"),
            "status": worker.get("status", "idle"),
            "queued_jobs": int(metrics.get("queued_jobs", 0)),
            "active_jobs": int(metrics.get("active_jobs", 0)),
            "queue_length": int(metrics.get("queue_length", metrics.get("queued_jobs", 0) + metrics.get("active_jobs", 0))),
            "processed_jobs": int(metrics.get("processed_jobs", 0)),
            "avg_processing_ms": float(metrics.get("avg_processing_ms", 0.0)),
            "last_duration_ms": float(metrics.get("last_duration_ms", 0.0)),
            "mem_usage_mb": float(metrics.get("mem_usage_mb", 0.0)),
            "last_error": str(metrics.get("last_error", "")),
            "endpoint_url": worker.get("endpoint_url", ""),
            "lease_ttl_seconds": int(worker.get("lease_ttl_seconds", 0)),
            "last_seen_at": float(worker.get("last_seen_at", 0.0)),
        }
