"""
FORGE Memory Graph FTS5 & Multi-Word Search Unit Tests
======================================================
Verifies FTS5 virtual table initialization, data synchronization,
relevance-ranked queries, and fallback multi-LIKE search algorithms.

Run:
    python -m unittest tests.unit.test_fts_memory -v
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from forge.memory.graph import MemoryGraph


class TestFTSMemory(unittest.TestCase):
    """Verifies that MemoryGraph FTS5 and fallback searches work correctly."""

    def setUp(self) -> None:
        # Use a temporary SQLite database for testing memory isolated from main memory.db
        self.db_fd, self.db_path = tempfile.mkstemp()
        self.mem = MemoryGraph(db_path=Path(self.db_path))

    def tearDown(self) -> None:
        self.mem.close()
        os.close(self.db_fd)
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def test_fts_supported_or_fallback_set(self) -> None:
        # Verify that _fts_supported flag is set (either True or False, no crash)
        self.assertIn(self.mem._fts_supported, [True, False])

    def test_remember_and_syncs_to_fts(self) -> None:
        self.mem.remember("project:forge", "FORGE is a self-evolving free AI agent system.")
        self.mem.remember("project:forge", "It is built in Python and runs locally.")
        
        # Recall by query matching terms
        res = self.mem.recall(query="free agent", subject="project:forge", as_context=False)
        self.assertIsInstance(res, list)
        self.assertTrue(len(res) >= 1)
        self.assertIn("self-evolving free AI agent system", res[0]["content"])

    def test_multi_word_search_query(self) -> None:
        self.mem.remember("user", "Larbi prefers Python over Javascript coding.")
        self.mem.remember("user", "Larbi likes to cook pasta on weekends.")
        
        # Querying multi-word "Python coding" should match only the first observation
        res = self.mem.recall(query="Python coding", subject="user", as_context=False)
        self.assertEqual(len(res), 1)
        self.assertIn("Python over Javascript", res[0]["content"])

    def test_fts_fallback_multi_like_search(self) -> None:
        # Force fallback mode to test multi-LIKE query path
        self.mem._fts_supported = False
        
        self.mem.remember("user", "Larbi prefers clean Python code without comments.")
        self.mem.remember("user", "Larbi lives in Morocco.")
        
        # Searching "Python code" should match only the first one
        res = self.mem.recall(query="Python code", subject="user", as_context=False)
        self.assertEqual(len(res), 1)
        self.assertIn("clean Python code", res[0]["content"])

    def test_retroactive_sync_existing_data(self) -> None:
        # Create a database, insert observations directly, then load a new MemoryGraph instance to trigger sync
        self.mem.remember("user", "Existing observation before FTS init.")
        self.mem.close()
        
        # Load a new instance on the same DB path - should sync existing observation into FTS5
        new_mem = MemoryGraph(db_path=Path(self.db_path))
        try:
            res = new_mem.recall(query="Existing observation", subject="user", as_context=False)
            self.assertEqual(len(res), 1)
            self.assertEqual(res[0]["content"], "Existing observation before FTS init.")
        finally:
            new_mem.close()


if __name__ == "__main__":
    unittest.main()
