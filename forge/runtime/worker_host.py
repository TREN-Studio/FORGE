from __future__ import annotations

import asyncio
import contextlib
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aiohttp import ClientSession, ClientTimeout, web

from forge.brain.worker_executor import WorkerTaskExecutor
from forge.brain.worker_protocol import WorkerHeartbeat, WorkerRegistration, WorkerTask
from forge.brain.worker_runtime import DEFAULT_SERVICE_LANES, _memory_usage_mb
from forge.runtime.lanes import LaneQueueManager


@dataclass(slots=True)
class WorkerHostSettings:
    host: str = "127.0.0.1"
    port: int = 18895
    gateway_url: str = "http://127.0.0.1:18789"
    gateway_token: str = ""
    worker_id: str = ""
    process_mode: str = "process"
    services: list[str] = field(default_factory=lambda: list(DEFAULT_SERVICE_LANES.keys()))
    service_lanes: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_SERVICE_LANES))
    heartbeat_interval_seconds: int = 5
    lease_ttl_seconds: int = 20
    workspace_root: Path = field(default_factory=lambda: Path.cwd().resolve())

    def resolved_worker_id(self) -> str:
        if self.worker_id:
            return self.worker_id
        hostname = socket.gethostname().lower()
        return f"worker-{hostname}-{self.port}"

    def endpoint_url(self) -> str:
        return f"http://{self.host}:{self.port}"


class ForgeWorkerHost:
    def __init__(self, settings: WorkerHostSettings) -> None:
        self.settings = settings
        self.worker_id = settings.resolved_worker_id()
        self._executor = WorkerTaskExecutor()
        self._lanes = LaneQueueManager()
        self._heartbeat_task: asyncio.Task | None = None
        self._runner: web.AppRunner | None = None

    async def start(self) -> None:
        app = self._create_app()
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.settings.host, self.settings.port)
        await site.start()
        await self._register()
        await self._send_heartbeat()
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop(), name=f"forge-worker-heartbeat-{self.worker_id}")

    async def stop(self) -> None:
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._heartbeat_task
        await self._lanes.close()
        if self._runner is not None:
            await self._runner.cleanup()

    async def _register(self) -> None:
        payload = WorkerRegistration(
            worker_id=self.worker_id,
            endpoint_url=self.settings.endpoint_url(),
            services=list(self.settings.services),
            capabilities={"workspace_root": str(self.settings.workspace_root)},
            process_mode=self.settings.process_mode,
            lease_ttl_seconds=self.settings.lease_ttl_seconds,
            metadata={"host": self.settings.host, "port": self.settings.port},
        )
        headers = {"Authorization": f"Bearer {self.settings.gateway_token}"} if self.settings.gateway_token else {}
        last_error = ""
        for _ in range(12):
            try:
                async with ClientSession(timeout=ClientTimeout(total=20)) as session:
                    async with session.post(
                        self.settings.gateway_url.rstrip("/") + "/api/workers/register",
                        headers=headers,
                        json=payload.model_dump(mode="json"),
                    ) as response:
                        if response.status >= 400:
                            raise RuntimeError(await response.text())
                return
            except Exception as exc:
                last_error = str(exc)
                await asyncio.sleep(1)
        raise RuntimeError(f"Worker registration failed after retries: {last_error}")

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(self.settings.heartbeat_interval_seconds)
            try:
                await self._send_heartbeat()
            except Exception:
                continue

    async def _send_heartbeat(self) -> None:
        heartbeat = WorkerHeartbeat(
            worker_id=self.worker_id,
            status=self._overall_status(),
            metrics=self._metrics_snapshot(),
            lease_ttl_seconds=self.settings.lease_ttl_seconds,
        )
        headers = {"Authorization": f"Bearer {self.settings.gateway_token}"} if self.settings.gateway_token else {}
        async with ClientSession(timeout=ClientTimeout(total=20)) as session:
            async with session.post(
                self.settings.gateway_url.rstrip("/") + "/api/workers/heartbeat",
                headers=headers,
                json=heartbeat.model_dump(mode="json"),
            ) as response:
                if response.status >= 400:
                    raise RuntimeError(await response.text())

    def _create_app(self) -> web.Application:
        app = web.Application()

        async def health_handler(_: web.Request) -> web.Response:
            return web.json_response(
                {
                    "worker_id": self.worker_id,
                    "status": self._overall_status(),
                    "metrics": self._metrics_snapshot(),
                }
            )

        async def execute_handler(request: web.Request) -> web.Response:
            task = WorkerTask.model_validate(await request.json())
            lane_id = await self._choose_lane(task.service_name)

            async def lane_job() -> dict[str, Any]:
                result = await asyncio.to_thread(self._executor.execute, task, worker_id=self.worker_id)
                return result.model_dump(mode="json")

            try:
                payload = await self._lanes.submit(lane_id, lane_job)
                await self._send_heartbeat()
                return web.json_response(payload)
            except Exception as exc:
                return web.json_response({"error": str(exc)}, status=500)

        app.add_routes(
            [
                web.get("/health", health_handler),
                web.post("/api/worker/execute", execute_handler),
            ]
        )
        return app

    async def _choose_lane(self, service_name: str) -> str:
        lane_count = max(1, self.settings.service_lanes.get(service_name, 1))
        lane_ids = [f"{service_name}:{index}" for index in range(lane_count)]
        snapshot = {lane["lane_id"]: lane for lane in self._lanes.snapshot()}
        ranked = []
        for lane_id in lane_ids:
            metrics = snapshot.get(lane_id, {"queue_length": 0, "processed_jobs": 0})
            ranked.append((int(metrics.get("queue_length", 0)), int(metrics.get("processed_jobs", 0)), lane_id))
        ranked.sort(key=lambda item: (item[0], item[1], item[2]))
        return ranked[0][2]

    def _overall_status(self) -> str:
        lanes = self._lanes.snapshot()
        if any(lane.get("last_error") for lane in lanes):
            return "failed"
        if any(int(lane.get("queue_length", 0)) > 4 for lane in lanes):
            return "overloaded"
        if any(int(lane.get("active_jobs", 0)) > 0 for lane in lanes):
            return "busy"
        return "idle"

    def _metrics_snapshot(self) -> dict[str, Any]:
        lanes = self._lanes.snapshot()
        processed = sum(int(lane.get("processed_jobs", 0)) for lane in lanes)
        avg_processing = (
            sum(float(lane.get("avg_processing_ms", 0.0)) for lane in lanes if int(lane.get("processed_jobs", 0)) > 0)
            / max(1, len([lane for lane in lanes if int(lane.get("processed_jobs", 0)) > 0]))
        ) if lanes else 0.0
        return {
            "active_jobs": sum(int(lane.get("active_jobs", 0)) for lane in lanes),
            "queued_jobs": sum(int(lane.get("queued_jobs", 0)) for lane in lanes),
            "queue_length": sum(int(lane.get("queue_length", 0)) for lane in lanes),
            "processed_jobs": processed,
            "avg_processing_ms": round(avg_processing, 2),
            "mem_usage_mb": _memory_usage_mb(),
            "lanes": lanes,
        }


def run_worker_host(settings: WorkerHostSettings) -> None:
    async def _main() -> None:
        host = ForgeWorkerHost(settings)
        await host.start()
        try:
            while True:
                await asyncio.sleep(3600)
        finally:
            await host.stop()

    asyncio.run(_main())
