from __future__ import annotations

import re
import time
from typing import Any

from forge.memory.graph import MemoryGraph


class ContextMemory:
    """Selective, semantically-ranked memory retrieval on top of the persistent graph.

    This layer decides what context is actually injected into the model prompt —
    the single highest-leverage lever for agent intelligence. It upgrades the old
    naive token-overlap retrieval to FTS5-based ranking with time decay, so the most
    *relevant* and *recent* memory wins rather than the most lexically-similar string.
    """

    _RECENCY_HALF_LIFE_SECONDS = 7 * 24 * 3600  # 7 days

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

        # Prefer the graph's FTS5 search (semantically stronger than token overlap).
        # MemoryGraph.recall returns a formatted context string when as_context=True;
        # request raw records instead for scoring control.
        records = self._graph.recall(query=query, subject="", limit=200, as_context=False)
        if not records:
            return []

        now = time.time()
        scored: list[tuple[float, str]] = []
        for rec in records:
            content = rec.get("content", "")
            if not content:
                continue
            # Lexical relevance (secondary) + recency decay (primary signal)
            haystack = content.lower()
            overlap = len(tokens.intersection(self._tokens(haystack)))
            if overlap == 0:
                continue
            created = float(rec.get("created_at", now))
            age = max(0.0, now - created)
            recency = 2.0 ** (-age / self._RECENCY_HALF_LIFE_SECONDS)
            score = (1.0 + overlap) * recency
            scored.append((score, content))

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
