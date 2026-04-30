from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
import time
from typing import Any

from forge.brain.operator import ForgeOperator
from forge.brain.orchestrator import MissionOrchestrator
from forge.config.settings import OperatorSettings
from forge.core.session import ForgeSession
from forge.runtime.contracts import AgentReply, GatewayEnvelope
from forge.runtime.heartbeat import HeartbeatDaemon, ScheduledTask
from forge.runtime.lanes import LaneQueueManager
from forge.runtime.markdown_memory import MarkdownMemoryStore
from forge.runtime.state_store import PersistentStateStore


@dataclass(slots=True)
class AgentRuntimeSettings:
    workspace_root: Path = field(default_factory=lambda: Path.cwd().resolve())
    runtime_root: Path = field(default_factory=lambda: (Path.home() / ".forge" / "runtime"))
    recent_transcript_limit: int = 8
    relevant_note_limit: int = 4
    enable_heartbeat: bool = True
    discovery_interval_seconds: int = 24 * 60 * 60
    memory_summary_interval_seconds: int = 6 * 60 * 60


class ForgeAgentRuntime:
    """Gateway-facing runtime with serial lane execution and markdown-first memory."""

    def __init__(self, settings: AgentRuntimeSettings | None = None) -> None:
        self.settings = settings or AgentRuntimeSettings()
        self.memory = MarkdownMemoryStore(self.settings.runtime_root)
        self.lanes = LaneQueueManager()
        self.heartbeat = HeartbeatDaemon()
        self.operator_settings = OperatorSettings(enable_memory=False, workspace_root=self.settings.workspace_root)
        self.state_store = PersistentStateStore(
            self.operator_settings.state_db_path,
            encryption_key_path=self.operator_settings.approval_key_path,
        )
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        MissionOrchestrator._ensure_cluster(self.state_store, self.operator_settings)
        if self.settings.enable_heartbeat:
            self._register_heartbeat_tasks()
            await self.heartbeat.start()
        self._started = True

    async def stop(self) -> None:
        await self.heartbeat.stop()
        await self.lanes.close()
        self._started = False

    async def handle_envelope(self, envelope: GatewayEnvelope) -> AgentReply:
        await self.start()
        lane = envelope.normalized_lane()
        return await self.lanes.submit(lane, lambda: self._execute(envelope, lane))

    def snapshot(self) -> dict[str, Any]:
        return {
            "lanes": self.lanes.snapshot(),
            "workers": MissionOrchestrator.worker_snapshot(),
            "approvals": MissionOrchestrator.approvals_snapshot(),
            "memory": self.memory.health(),
            "heartbeat": self.heartbeat.snapshot(),
        }

    async def run_heartbeat_once(self) -> list[dict[str, Any]]:
        await self.start()
        reports = await self.heartbeat.tick_once()
        return [report.model_dump(mode="json") for report in reports]

    async def _execute(self, envelope: GatewayEnvelope, lane: str) -> AgentReply:
        started = time.monotonic()
        memory_bundle = self.memory.build_context(
            query=envelope.content,
            session_id=envelope.session_id,
            recent_limit=self.settings.recent_transcript_limit,
            note_limit=self.settings.relevant_note_limit,
        )

        self.memory.append_event(
            session_id=envelope.session_id,
            request_id=envelope.request_id,
            role="user",
            content=envelope.content,
            channel=envelope.channel,
            lane=lane,
            metadata=envelope.metadata,
        )

        operator_payload = await asyncio.to_thread(
            self._run_operator_sync,
            envelope.content,
            memory_bundle.context_markdown,
            envelope.confirmed,
            envelope.dry_run,
        )

        self.memory.append_event(
            session_id=envelope.session_id,
            request_id=envelope.request_id,
            role="assistant",
            content=operator_payload["answer"],
            channel=envelope.channel,
            lane=lane,
            metadata={
                "objective": operator_payload["objective"],
                "validation_status": operator_payload["validation_status"],
                "best_next_action": operator_payload["best_next_action"],
            },
        )
        episode_path = self.memory.store_episode(
            session_id=envelope.session_id,
            request_id=envelope.request_id,
            user_request=envelope.content,
            reply=operator_payload,
        )
        operator_payload["artifacts"]["runtime_episode"] = {"episode_path": str(episode_path)}

        return AgentReply(
            request_id=envelope.request_id,
            session_id=envelope.session_id,
            lane=lane,
            channel=envelope.channel,
            status="completed" if operator_payload["validation_status"] != "failed" else "failed",
            objective=operator_payload["objective"],
            answer=operator_payload["answer"],
            validation_status=operator_payload["validation_status"],
            best_next_action=operator_payload["best_next_action"],
            approach_taken=operator_payload["approach_taken"],
            plan=operator_payload["plan"],
            step_results=operator_payload["step_results"],
            artifacts=operator_payload["artifacts"],
            memory_sources=memory_bundle.sources,
            memory_excerpt=memory_bundle.context_markdown[:2200],
            evidence_count=operator_payload["evidence_count"],
            processing_ms=round((time.monotonic() - started) * 1000, 2),
        )

    def _run_operator_sync(
        self,
        user_request: str,
        memory_context: str,
        confirmed: bool,
        dry_run: bool,
    ) -> dict[str, Any]:
        operator = ForgeOperator(settings=self.operator_settings)
        result = operator.handle(
            user_request,
            confirmed=confirmed,
            dry_run=dry_run,
            memory_context_override=memory_context,
        )
        payload = result.model_dump(mode="json")
        payload["answer"] = operator.composer.compose(result)
        payload["evidence_count"] = sum(len(step.get("evidence", [])) for step in payload.get("step_results", []))
        return payload

    def _register_heartbeat_tasks(self) -> None:
        if self.heartbeat.snapshot():
            return
        self.heartbeat.register(
            ScheduledTask(
                name="provider_discovery",
                interval_seconds=self.settings.discovery_interval_seconds,
                handler=self._provider_discovery_task,
                run_immediately=True,
            )
        )
        self.heartbeat.register(
            ScheduledTask(
                name="memory_summary",
                interval_seconds=self.settings.memory_summary_interval_seconds,
                handler=self._memory_summary_task,
                run_immediately=True,
            )
        )

    async def _provider_discovery_task(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._provider_discovery_sync)

    def _provider_discovery_sync(self) -> dict[str, Any]:
        session = ForgeSession(memory=False)
        report = session.discover_models()
        return {
            "discovered": report.get("discovered", 0),
            "attached": report.get("attached", 0),
            "providers": report.get("providers", {}),
        }

    def _memory_summary_task(self) -> dict[str, Any]:
        summary_path = self.memory.write_daily_summary()
        return {"summary_path": str(summary_path)}
