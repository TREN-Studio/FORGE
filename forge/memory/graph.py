"""
FORGE Memory Graph
===================
Not just conversation history. A living knowledge graph.

Every interaction teaches FORGE something permanent:
  - Who you are and what you work on
  - Your coding style, preferences, tech stack
  - Your projects, their structure, their goals
  - Facts you've told it, patterns it has noticed

This is what transforms FORGE from a stateless chatbot
into an entity that genuinely knows you over time.

Storage: SQLite (local) — your data never leaves your machine.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

# We use plain sqlite3 to avoid heavy ORM dependency at import time
import sqlite3


_DB_PATH = Path.home() / ".forge" / "memory.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entities (
    id          TEXT PRIMARY KEY,
    type        TEXT NOT NULL,
    name        TEXT NOT NULL,
    data        TEXT NOT NULL DEFAULT '{}',
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS relations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    from_id     TEXT NOT NULL,
    relation    TEXT NOT NULL,
    to_id       TEXT NOT NULL,
    weight      REAL NOT NULL DEFAULT 1.0,
    created_at  REAL NOT NULL,
    UNIQUE(from_id, relation, to_id)
);

CREATE TABLE IF NOT EXISTS observations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id   TEXT NOT NULL,
    content     TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'conversation',
    confidence  REAL NOT NULL DEFAULT 1.0,
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    id          TEXT PRIMARY KEY,
    title       TEXT,
    started_at  REAL NOT NULL,
    last_msg_at REAL NOT NULL,
    msg_count   INTEGER NOT NULL DEFAULT 0,
    summary     TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    model_used      TEXT,
    provider_used   TEXT,
    latency_ms      REAL,
    tokens          INTEGER,
    created_at      REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_obs_entity   ON observations(entity_id);
CREATE INDEX IF NOT EXISTS idx_msg_conv     ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_rel_from     ON relations(from_id);
CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(type);
"""


class MemoryGraph:
    """
    Persistent knowledge graph for FORGE.

    Usage:
        mem = MemoryGraph()
        mem.remember("user", "prefers Python over JavaScript")
        mem.remember("project:foodjot", "uses WordPress, has 600+ articles")

        context = mem.recall(query="current project")
        # → "You are working on FoodJot Blog (WordPress, 600+ articles)..."
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self._path = db_path or _DB_PATH
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ── Entity CRUD ───────────────────────────────────────────────

    def upsert_entity(
        self,
        entity_type: str,
        name: str,
        data: dict[str, Any] | None = None,
    ) -> str:
        """Create or update an entity. Returns entity id."""
        eid  = self._make_id(entity_type, name)
        now  = time.time()
        data = data or {}
        existing = self._conn.execute(
            "SELECT id FROM entities WHERE id = ?", (eid,)
        ).fetchone()
        if existing:
            current_data = json.loads(
                self._conn.execute("SELECT data FROM entities WHERE id=?", (eid,))
                .fetchone()["data"]
            )
            current_data.update(data)
            self._conn.execute(
                "UPDATE entities SET data=?, updated_at=? WHERE id=?",
                (json.dumps(current_data), now, eid),
            )
        else:
            self._conn.execute(
                "INSERT INTO entities (id,type,name,data,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (eid, entity_type, name, json.dumps(data), now, now),
            )
        self._conn.commit()
        return eid

    def add_relation(
        self,
        from_name:    str,
        from_type:    str,
        relation:     str,
        to_name:      str,
        to_type:      str,
        weight:       float = 1.0,
    ) -> None:
        """Add a directional relation: (from) -[relation]-> (to)"""
        from_id = self.upsert_entity(from_type, from_name)
        to_id   = self.upsert_entity(to_type,   to_name)
        now = time.time()
        self._conn.execute(
            """INSERT INTO relations (from_id,relation,to_id,weight,created_at)
               VALUES(?,?,?,?,?)
               ON CONFLICT(from_id,relation,to_id) DO UPDATE SET weight=excluded.weight""",
            (from_id, relation, to_id, weight, now),
        )
        self._conn.commit()

    # ── Observations ──────────────────────────────────────────────

    def remember(
        self,
        subject:    str,
        content:    str,
        source:     str  = "conversation",
        confidence: float = 1.0,
    ) -> None:
        """
        Store an observation about a subject.

        mem.remember("user", "prefers concise code without comments")
        mem.remember("project:forge", "Python project, MIT license")
        """
        entity_id = self._ensure_entity(subject)
        self._conn.execute(
            "INSERT INTO observations (entity_id,content,source,confidence,created_at) VALUES(?,?,?,?,?)",
            (entity_id, content, source, confidence, time.time()),
        )
        self._conn.commit()

    def recall(
        self,
        query:      str  = "",
        subject:    str  = "user",
        limit:      int  = 20,
        as_context: bool = True,
    ) -> str | list[dict]:
        """
        Retrieve relevant memories.

        Returns a formatted context string by default,
        or a list of dicts if as_context=False.
        """
        entity_id = self._find_entity(subject)
        if entity_id is None and query:
            rows = self._search_observations(query, limit)
        elif entity_id:
            rows = self._conn.execute(
                """SELECT o.content, o.source, o.confidence, o.created_at, e.name
                   FROM observations o
                   JOIN entities e ON e.id = o.entity_id
                   WHERE o.entity_id = ?
                   ORDER BY o.created_at DESC LIMIT ?""",
                (entity_id, limit),
            ).fetchall()
        else:
            rows = []

        records = [dict(r) for r in rows]

        if not as_context:
            return records

        if not records:
            return ""

        lines = [f"[Memory about {subject}]"]
        for r in records:
            lines.append(f"• {r['content']}")
        return "\n".join(lines)

    def recall_all(self, limit: int = 30) -> str:
        """Get all recent memories as a system context block."""
        rows = self._conn.execute(
            """SELECT e.name, e.type, o.content, o.created_at
               FROM observations o
               JOIN entities e ON e.id = o.entity_id
               ORDER BY o.created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()

        if not rows:
            return ""

        grouped: dict[str, list[str]] = {}
        for r in rows:
            key = f"{r['type']}:{r['name']}"
            grouped.setdefault(key, []).append(r["content"])

        lines = ["[FORGE Memory Context]"]
        for key, facts in grouped.items():
            lines.append(f"\n{key}:")
            for f in facts[:5]:
                lines.append(f"  • {f}")
        return "\n".join(lines)

    # ── Conversation Logging ──────────────────────────────────────

    def new_conversation(self, title: str | None = None) -> str:
        """Start a new conversation. Returns conversation id."""
        cid = hashlib.sha256(f"{time.time()}".encode()).hexdigest()[:16]
        now = time.time()
        self._conn.execute(
            "INSERT INTO conversations (id,title,started_at,last_msg_at) VALUES(?,?,?,?)",
            (cid, title, now, now),
        )
        self._conn.commit()
        return cid

    def log_message(
        self,
        conversation_id: str,
        role:            str,
        content:         str,
        model_used:      str | None = None,
        provider_used:   str | None = None,
        latency_ms:      float | None = None,
        tokens:          int | None = None,
    ) -> None:
        now = time.time()
        self._conn.execute(
            """INSERT INTO messages
               (conversation_id,role,content,model_used,provider_used,latency_ms,tokens,created_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (conversation_id, role, content, model_used, provider_used, latency_ms, tokens, now),
        )
        self._conn.execute(
            """UPDATE conversations
               SET last_msg_at=?, msg_count=msg_count+1 WHERE id=?""",
            (now, conversation_id),
        )
        self._conn.commit()

    def get_conversation_history(
        self,
        conversation_id: str,
        limit:           int = 50,
    ) -> list[dict]:
        rows = self._conn.execute(
            "SELECT role,content FROM messages WHERE conversation_id=? ORDER BY created_at LIMIT ?",
            (conversation_id, limit),
        ).fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in rows]

    def stats(self) -> dict:
        return {
            "entities":      self._conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0],
            "observations":  self._conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0],
            "conversations": self._conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0],
            "messages":      self._conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
        }

    # ── Internals ────────────────────────────────────────────────

    def _make_id(self, entity_type: str, name: str) -> str:
        return hashlib.sha256(f"{entity_type}:{name.lower()}".encode()).hexdigest()[:20]

    def _ensure_entity(self, subject: str) -> str:
        parts = subject.split(":", 1)
        etype = parts[0] if len(parts) == 2 else "general"
        ename = parts[1] if len(parts) == 2 else subject
        return self.upsert_entity(etype, ename)

    def _find_entity(self, subject: str) -> str | None:
        parts = subject.split(":", 1)
        etype = parts[0] if len(parts) == 2 else None
        ename = parts[1] if len(parts) == 2 else subject
        eid   = self._make_id(etype or "general", ename)
        row   = self._conn.execute("SELECT id FROM entities WHERE id=?", (eid,)).fetchone()
        if row:
            return row[0]
        # Fuzzy fallback: search by name
        row = self._conn.execute(
            "SELECT id FROM entities WHERE lower(name) LIKE ? LIMIT 1",
            (f"%{ename.lower()}%",),
        ).fetchone()
        return row[0] if row else None

    def _search_observations(self, query: str, limit: int) -> list:
        words = [w for w in query.lower().split() if len(w) > 3]
        if not words:
            return []
        like = f"%{words[0]}%"
        return self._conn.execute(
            """SELECT o.content, o.source, o.confidence, o.created_at, e.name
               FROM observations o JOIN entities e ON e.id=o.entity_id
               WHERE lower(o.content) LIKE ?
               ORDER BY o.created_at DESC LIMIT ?""",
            (like, limit),
        ).fetchall()

    def close(self) -> None:
        self._conn.close()
