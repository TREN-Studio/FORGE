from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
import time

from aiohttp import WSMsgType, web

from forge.brain.orchestrator import MissionOrchestrator
from forge.brain.worker_protocol import WorkerHeartbeat, WorkerRegistration
from forge.runtime.agent import AgentRuntimeSettings, ForgeAgentRuntime
from forge.runtime.contracts import GatewayEnvelope


@dataclass(slots=True)
class GatewaySettings:
    host: str = "127.0.0.1"
    port: int = 18789
    auth_token: str = ""
    requests_per_minute: int = 60


class GatewayGuard:
    def __init__(self, settings: GatewaySettings) -> None:
        self.settings = settings
        self._requests: dict[str, deque[float]] = defaultdict(deque)

    def authorize(self, request: web.Request) -> None:
        if self.settings.auth_token:
            token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
            query_token = request.query.get("token", "").strip()
            if token != self.settings.auth_token and query_token != self.settings.auth_token:
                raise web.HTTPUnauthorized(text="Invalid gateway token.")
        client = self._client_key(request)
        self._record_request(client)

    def _record_request(self, client: str) -> None:
        now = time.monotonic()
        queue = self._requests[client]
        while queue and now - queue[0] > 60:
            queue.popleft()
        if len(queue) >= self.settings.requests_per_minute:
            raise web.HTTPTooManyRequests(text="Gateway rate limit exceeded.")
        queue.append(now)

    @staticmethod
    def _client_key(request: web.Request) -> str:
        peer = request.remote or "unknown"
        forwarded = request.headers.get("X-Forwarded-For")
        return forwarded.split(",")[0].strip() if forwarded else peer


def create_app(
    runtime: ForgeAgentRuntime | None = None,
    *,
    gateway_settings: GatewaySettings | None = None,
    runtime_settings: AgentRuntimeSettings | None = None,
) -> web.Application:
    runtime = runtime or ForgeAgentRuntime(runtime_settings)
    settings = gateway_settings or GatewaySettings()
    guard = GatewayGuard(settings)
    app = web.Application()

    async def on_startup(_: web.Application) -> None:
        await runtime.start()

    async def on_cleanup(_: web.Application) -> None:
        await runtime.stop()

    async def health_handler(request: web.Request) -> web.Response:
        guard.authorize(request)
        return web.json_response(
            {
                "status": "ok",
                "gateway": {
                    "host": settings.host,
                    "port": settings.port,
                    "requests_per_minute": settings.requests_per_minute,
                },
                "runtime": runtime.snapshot(),
            }
        )

    async def message_handler(request: web.Request) -> web.Response:
        guard.authorize(request)
        data = await request.json()
        envelope = GatewayEnvelope.model_validate(data)
        if envelope.type == "ping":
            return web.json_response({"ok": True, "session_id": envelope.session_id})
        reply = await runtime.handle_envelope(envelope)
        return web.json_response(reply.model_dump(mode="json"))

    async def heartbeat_handler(request: web.Request) -> web.Response:
        guard.authorize(request)
        reports = await runtime.run_heartbeat_once()
        return web.json_response({"reports": reports})

    async def workers_handler(request: web.Request) -> web.Response:
        guard.authorize(request)
        return web.json_response({"workers": runtime.snapshot().get("workers", {})})

    async def worker_register_handler(request: web.Request) -> web.Response:
        guard.authorize(request)
        payload = WorkerRegistration.model_validate(await request.json())
        MissionOrchestrator.register_worker(payload)
        return web.json_response({"ok": True, "worker_id": payload.worker_id})

    async def worker_heartbeat_handler(request: web.Request) -> web.Response:
        guard.authorize(request)
        payload = WorkerHeartbeat.model_validate(await request.json())
        MissionOrchestrator.heartbeat_worker(payload)
        return web.json_response({"ok": True, "worker_id": payload.worker_id})

    async def approvals_handler(request: web.Request) -> web.Response:
        guard.authorize(request)
        return web.json_response({"approvals": MissionOrchestrator.approvals_snapshot()})

    async def approval_status_handler(request: web.Request) -> web.Response:
        guard.authorize(request)
        approval = MissionOrchestrator.approval_status(request.match_info["approval_id"])
        if approval is None:
            raise web.HTTPNotFound(text="Approval not found.")
        return web.json_response(approval)

    async def approval_decision_handler(request: web.Request) -> web.Response:
        guard.authorize(request)
        approval_id = request.match_info["approval_id"]
        payload = await request.json()
        notes = str(payload.get("notes", "")).strip()
        if request.match_info["decision"] == "approve":
            result = MissionOrchestrator.approve(approval_id, notes=notes)
        else:
            result = MissionOrchestrator.reject(approval_id, notes=notes)
        if result is None:
            raise web.HTTPNotFound(text="Approval not found.")
        return web.json_response(result)

    async def ws_handler(request: web.Request) -> web.StreamResponse:
        guard.authorize(request)
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        await ws.send_json({"type": "ready", "status": "connected"})

        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                envelope = GatewayEnvelope.model_validate_json(msg.data)
                if envelope.type == "ping":
                    await ws.send_json({"type": "pong", "session_id": envelope.session_id})
                    continue
                await ws.send_json(
                    {
                        "type": "accepted",
                        "request_id": envelope.request_id,
                        "session_id": envelope.session_id,
                        "lane": envelope.normalized_lane(),
                    }
                )
                reply = await runtime.handle_envelope(envelope)
                await ws.send_json({"type": "result", "payload": reply.model_dump(mode="json")})
            elif msg.type == WSMsgType.ERROR:
                break
        return ws

    app.add_routes(
        [
            web.get("/health", health_handler),
            web.get("/api/workers", workers_handler),
            web.post("/api/workers/register", worker_register_handler),
            web.post("/api/workers/heartbeat", worker_heartbeat_handler),
            web.post("/api/message", message_handler),
            web.post("/api/heartbeat", heartbeat_handler),
            web.get("/api/approvals", approvals_handler),
            web.get("/api/approvals/{approval_id}", approval_status_handler),
            web.post("/api/approvals/{approval_id}/{decision:approve|reject}", approval_decision_handler),
            web.get("/ws", ws_handler),
        ]
    )
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


def run_gateway(
    *,
    gateway_settings: GatewaySettings | None = None,
    runtime_settings: AgentRuntimeSettings | None = None,
) -> None:
    settings = gateway_settings or GatewaySettings()
    app = create_app(gateway_settings=settings, runtime_settings=runtime_settings)
    web.run_app(app, host=settings.host, port=settings.port)
