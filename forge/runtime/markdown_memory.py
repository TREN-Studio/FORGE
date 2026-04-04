from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any


MAX_MARKDOWN_CHARS = 6_000


@dataclass(slots=True)
class MemoryContextBundle:
    context_markdown: str
    sources: list[str]
    recent_events: list[dict[str, Any]]


class MarkdownMemoryStore:
    """Local-first memory using Markdown for durable notes and JSONL for audit logs."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.docs_root = self.root / "docs"
        self.episodes_root = self.root / "episodes"
        self.transcripts_root = self.root / "transcripts"
        self.heartbeat_root = self.root / "heartbeat"

        for path in (self.root, self.docs_root, self.episodes_root, self.transcripts_root, self.heartbeat_root):
            path.mkdir(parents=True, exist_ok=True)

        self.soul_path = self.docs_root / "SOUL.md"
        self.memory_path = self.docs_root / "MEMORY.md"
        self._ensure_defaults()

    def append_event(
        self,
        *,
        session_id: str,
        request_id: str,
        role: str,
        content: str,
        channel: str,
        lane: str,
        metadata: dict[str, Any] | None = None,
    ) -> Path:
        metadata = metadata or {}
        timestamp = datetime.now(timezone.utc)
        target = self.transcripts_root / f"{timestamp:%Y-%m-%d}.jsonl"
        event = {
            "timestamp": timestamp.isoformat(),
            "session_id": session_id,
            "request_id": request_id,
            "role": role,
            "channel": channel,
            "lane": lane,
            "content": content,
            "metadata": metadata,
        }
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        return target

    def build_context(
        self,
        *,
        query: str,
        session_id: str,
        recent_limit: int = 8,
        note_limit: int = 4,
    ) -> MemoryContextBundle:
        recent_events = self._recent_session_events(session_id, limit=recent_limit)
        note_hits = self._search_markdown(query, limit=note_limit)
        sources = [str(self.soul_path), str(self.memory_path)]
        sources.extend(str(item["path"]) for item in note_hits)

        sections: list[str] = []
        soul = self._read_markdown(self.soul_path)
        memory = self._read_markdown(self.memory_path)
        if soul:
            sections.append(f"## SOUL\n{soul}")
        if memory:
            sections.append(f"## MEMORY\n{memory}")
        if recent_events:
            transcript_lines = [
                f"- {item['role']} | {item['content'][:220]}"
                for item in recent_events
            ]
            sections.append("## RECENT SESSION\n" + "\n".join(transcript_lines))
        if note_hits:
            note_lines = [
                f"### {item['path'].name}\n{item['excerpt']}"
                for item in note_hits
            ]
            sections.append("## RELEVANT EPISODES\n" + "\n\n".join(note_lines))

        return MemoryContextBundle(
            context_markdown="\n\n".join(sections).strip(),
            sources=list(dict.fromkeys(sources)),
            recent_events=recent_events,
        )

    def store_episode(
        self,
        *,
        session_id: str,
        request_id: str,
        user_request: str,
        reply: dict[str, Any],
    ) -> Path:
        timestamp = datetime.now(timezone.utc)
        safe_slug = re.sub(r"[^a-z0-9]+", "-", user_request.lower())[:50].strip("-") or "mission"
        target = self.episodes_root / f"{timestamp:%Y%m%d-%H%M%S}-{safe_slug}-{request_id[:8]}.md"

        plan = reply.get("plan", {})
        step_results = reply.get("step_results", [])
        lines = [
            f"# Session {session_id}",
            "",
            f"- Request ID: `{request_id}`",
            f"- Validation: `{reply.get('validation_status', 'unknown')}`",
            f"- Objective: {reply.get('objective', '')}",
            "",
            "## User Request",
            user_request,
            "",
            "## Final Answer",
            reply.get("answer", ""),
            "",
            "## Best Next Action",
            reply.get("best_next_action", ""),
            "",
            "## Plan",
        ]
        for step in plan.get("steps", []):
            lines.append(f"- {step['id']}: {step['action']} | skill={step.get('skill') or 'reasoning'}")
        if not plan.get("steps"):
            lines.append("- No structured plan was recorded.")

        lines.extend(["", "## Step Results"])
        for step in step_results:
            lines.append(
                f"- {step.get('step_id')}: status={step.get('status')} "
                f"skill={step.get('skill')} attempts={step.get('attempts')}"
            )
        if not step_results:
            lines.append("- No step results were recorded.")

        target.write_text("\n".join(lines), encoding="utf-8")
        return target

    def write_daily_summary(self) -> Path:
        transcript_files = sorted(self.transcripts_root.glob("*.jsonl"))
        totals = {"messages": 0, "sessions": set(), "channels": set()}
        for path in transcript_files[-7:]:
            for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                if not raw_line.strip():
                    continue
                try:
                    item = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
                totals["messages"] += 1
                totals["sessions"].add(item.get("session_id", ""))
                totals["channels"].add(item.get("channel", ""))

        target = self.heartbeat_root / f"daily-summary-{datetime.now(timezone.utc):%Y-%m-%d}.md"
        target.write_text(
            "\n".join(
                [
                    "# FORGE Runtime Daily Summary",
                    "",
                    f"- Messages logged: {totals['messages']}",
                    f"- Sessions observed: {len([item for item in totals['sessions'] if item])}",
                    f"- Channels observed: {', '.join(sorted(item for item in totals['channels'] if item)) or 'none'}",
                    "",
                    "This summary is generated automatically by Heartbeat.",
                ]
            ),
            encoding="utf-8",
        )
        return target

    def health(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "episodes": len(list(self.episodes_root.glob("*.md"))),
            "transcript_files": len(list(self.transcripts_root.glob("*.jsonl"))),
        }

    def _ensure_defaults(self) -> None:
        if not self.soul_path.exists():
            self.soul_path.write_text(
                "# FORGE SOUL\n\nFORGE is a serious autonomous operator. It must prefer truth, structure, safety, and evidence over style.\n",
                encoding="utf-8",
            )
        if not self.memory_path.exists():
            self.memory_path.write_text(
                "# FORGE MEMORY\n\n- Preferred interaction style: concise, execution-focused.\n- Product goal: build a serious autonomous agent platform.\n",
                encoding="utf-8",
            )

    def _recent_session_events(self, session_id: str, limit: int) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for path in sorted(self.transcripts_root.glob("*.jsonl"), reverse=True):
            for raw_line in reversed(path.read_text(encoding="utf-8", errors="ignore").splitlines()):
                if len(events) >= limit:
                    return list(reversed(events))
                if not raw_line.strip():
                    continue
                try:
                    item = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
                if item.get("session_id") == session_id:
                    events.append(item)
        return list(reversed(events))

    def _search_markdown(self, query: str, limit: int) -> list[dict[str, Any]]:
        tokens = self._tokens(query)
        if not tokens:
            return []

        hits: list[dict[str, Any]] = []
        for path in sorted([self.soul_path, self.memory_path, *self.episodes_root.glob("*.md")]):
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")[:MAX_MARKDOWN_CHARS]
            lowered = text.lower()
            score = sum(lowered.count(token) for token in tokens)
            if path in {self.soul_path, self.memory_path}:
                score += 1
            if score <= 0:
                continue
            hits.append(
                {
                    "path": path,
                    "score": score,
                    "excerpt": self._best_excerpt(text, tokens),
                }
            )
        hits.sort(key=lambda item: (-item["score"], str(item["path"])))
        return hits[:limit]

    @staticmethod
    def _best_excerpt(text: str, tokens: list[str]) -> str:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        for line in lines:
            lowered = line.lower()
            if any(token in lowered for token in tokens):
                return line[:320]
        return lines[0][:320] if lines else ""

    @staticmethod
    def _read_markdown(path: Path) -> str:
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8", errors="ignore")[:2000].strip()

    @staticmethod
    def _tokens(text: str) -> list[str]:
        tokens = re.findall(r"[\w/-]+", text.lower(), flags=re.UNICODE)
        return [token for token in dict.fromkeys(tokens) if len(token) >= 3]
