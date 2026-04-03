from __future__ import annotations

import re
from typing import Any

from forge.memory.graph import MemoryGraph


class ContextMemory:
    """Selective memory retrieval on top of the persistent graph."""

    def __init__(self, graph: MemoryGraph | None = None) -> None:
        self._graph = graph or MemoryGraph()
        self._session_state: dict[str, Any] = {}

    def set_session_value(self, key: str, value: Any) -> None:
        self._session_state[key] = value

    def get_session_value(self, key: str, default: Any = None) -> Any:
        return self._session_state.get(key, default)

    def remember_preference(self, name: str, value: str) -> None:
        self._graph.remember("preference:user", f"{name}={value}")

    def remember_constraint(self, value: str) -> None:
        self._graph.remember("constraint:active", value)

    def remember_execution(self, skill_name: str, summary: str) -> None:
        self._graph.remember(f"skill:{skill_name}", summary, source="execution")

    def retrieve_relevant(self, query: str, limit: int = 5) -> list[str]:
        tokens = self._tokens(query)
        if not tokens:
            return []

        rows = self._graph._conn.execute(
            """
            SELECT o.content, e.name, e.type
            FROM observations o
            JOIN entities e ON e.id = o.entity_id
            ORDER BY o.created_at DESC
            LIMIT 200
            """
        ).fetchall()

        scored: list[tuple[int, str]] = []
        for row in rows:
            content = row["content"]
            haystack = f"{row['type']} {row['name']} {content}".lower()
            overlap = len(tokens.intersection(self._tokens(haystack)))
            if overlap:
                scored.append((overlap, content))

        scored.sort(key=lambda item: item[0], reverse=True)
        return [item[1] for item in scored[:limit]]

    def build_context(self, query: str, limit: int = 5) -> str:
        relevant = self.retrieve_relevant(query, limit=limit)
        if not relevant:
            return ""
        return "\n".join(f"- {item}" for item in relevant)

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return set(re.findall(r"[a-z0-9_/-]+", text.lower()))
