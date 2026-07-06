"""Tests for episodic memory Phase 5 — semantic search and recall planner."""

from __future__ import annotations

import json
import math
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from buddy_tools.core.registry import ALL_TOOL_DEFINITIONS, execute_tool
from buddy_tools.episodic.config import EpisodicConfig
from buddy_tools.episodic.consolidation import consolidate_session
from buddy_tools.episodic.index import (
    index_dir,
    load_index_entries,
    rebuild_episodic_index,
    search_index,
)
from buddy_tools.episodic.manager import EpisodicSessionManager, reset_episodic_for_tests
from buddy_tools.episodic.paths import bucket_keys
from buddy_tools.episodic.planner import plan_episodic_recall
from buddy_tools.episodic.retrieval import (
    EPISODIC_TOOL_NAMES,
    execute_episodic_tool,
    search_episodic_memory,
)
from buddy_tools.episodic.turns import EpisodicTurnRecord


def _mock_llm(system: str, user: str) -> str:
    system_lower = system.lower()
    if "summarize conversation" in system_lower:
        if "pasta" in user.lower() or "cooking" in user.lower():
            return json.dumps(
                {"summary": "User asked about pasta recipes.", "topics": ["cooking", "pasta"]}
            )
        if "telegram" in user.lower():
            return json.dumps(
                {"summary": "User reported a Telegram bot bug.", "topics": ["telegram", "bug"]}
            )
        return json.dumps(
            {"summary": "User asked about the weather in Boston.", "topics": ["weather", "boston"]}
        )
    if "merge child summaries" in system_lower or "merge these" in user.lower():
        return json.dumps({"summary": "Consolidated rollup summary."})
    if "durable facts" in system_lower:
        return json.dumps({"facts": []})
    raise AssertionError(f"Unexpected LLM prompt: {system[:80]!r}")


def _mock_embed(texts: list[str]) -> list[list[float]]:
    dim = 8
    vectors: list[list[float]] = []
    for text in texts:
        vec = [0.0] * dim
        for token in text.lower().split():
            idx = abs(hash(token)) % dim
            vec[idx] += 1.0
        norm = math.sqrt(sum(value * value for value in vec))
        if norm > 0:
            vec = [value / norm for value in vec]
        vectors.append(vec)
    return vectors


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self._current = start

    def now(self) -> datetime:
        return self._current

    def advance(self, delta: timedelta) -> None:
        self._current = self._current + delta


def _test_config() -> EpisodicConfig:
    return EpisodicConfig(
        idle_timeout_minutes=20,
        max_session_minutes=120,
        timezone="America/New_York",
        consolidation_delay_seconds=0,
        consolidation_retry_base_seconds=1,
    )


def _close_session_with_turns(
    manager: EpisodicSessionManager,
    *,
    user_text: str,
    assistant_text: str,
) -> tuple[Path, str]:
    manager.on_user_activity("voice")
    session = manager.current_session()
    assert session is not None
    session_dir = manager._session_dir
    assert session_dir is not None
    manager.log_turn(EpisodicTurnRecord(role="user", channel="voice", text=user_text))
    manager.log_turn(EpisodicTurnRecord(role="assistant", channel="voice", text=assistant_text))
    with patch("buddy_tools.episodic.manager.enqueue_session_consolidation"):
        manager.force_close("idle_timeout")
    return session_dir, session.session_id


def _build_consolidated_tree(memory_root: Path) -> dict[str, str]:
    clock = FakeClock(datetime(2026, 7, 5, 15, 0, 0, tzinfo=UTC))
    manager = EpisodicSessionManager(
        memory_root,
        "buddy",
        config=_test_config(),
        now_fn=clock.now,
    )
    day1_dir, day1_id = _close_session_with_turns(
        manager,
        user_text="What's the weather in Boston?",
        assistant_text="It's sunny today.",
    )
    consolidate_session(day1_dir, memory_root, "buddy", llm_fn=_mock_llm, embed_fn=_mock_embed)

    clock.advance(timedelta(days=1))
    day2_dir, day2_id = _close_session_with_turns(
        manager,
        user_text="Any good pasta recipes?",
        assistant_text="Try cacio e pepe.",
    )
    consolidate_session(day2_dir, memory_root, "buddy", llm_fn=_mock_llm, embed_fn=_mock_embed)

    clock.advance(timedelta(days=1))
    day3_dir, day3_id = _close_session_with_turns(
        manager,
        user_text="The Telegram bot stopped responding.",
        assistant_text="I'll look into that bug.",
    )
    consolidate_session(day3_dir, memory_root, "buddy", llm_fn=_mock_llm, embed_fn=_mock_embed)

    return {"day1_id": day1_id, "day2_id": day2_id, "day3_id": day3_id}


class EpisodicSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.memory_root = Path(self._tmpdir.name) / "memory"
        self.memory_root.mkdir()
        reset_episodic_for_tests()
        self._embed_patch = patch(
            "buddy_tools.episodic.index.embed_texts",
            side_effect=lambda texts, *, embed_fn=None: _mock_embed(texts),
        )
        self._embed_patch.start()
        self.session_ids = _build_consolidated_tree(self.memory_root)

    def tearDown(self) -> None:
        self._embed_patch.stop()
        reset_episodic_for_tests()
        self._tmpdir.cleanup()

    def test_index_created_on_consolidation(self) -> None:
        index_path = index_dir(self.memory_root, "buddy")
        self.assertTrue(index_path.is_dir())
        entries = load_index_entries(self.memory_root, "buddy")
        self.assertGreaterEqual(len(entries), 3)
        session_entries = [entry for entry in entries if entry.level == "session"]
        self.assertEqual(len(session_entries), 3)

    def test_semantic_search_ranks_relevant_session(self) -> None:
        hits = search_index(
            self.memory_root,
            "buddy",
            "Telegram bot bug",
            embed_fn=_mock_embed,
        )
        self.assertGreaterEqual(len(hits), 1)
        telegram_hits = [hit for hit in hits if hit.get("session_id") == self.session_ids["day3_id"]]
        self.assertEqual(len(telegram_hits), 1)
        self.assertGreater(telegram_hits[0]["score"], 0.0)
        self.assertIn("provenance", telegram_hits[0])

    def test_search_episodic_memory_includes_recall_plan(self) -> None:
        payload = search_episodic_memory(
            self.memory_root,
            "buddy",
            query="when did we discuss pasta",
        )
        self.assertEqual(payload["query"], "when did we discuss pasta")
        self.assertIn("recall_plan", payload)
        self.assertEqual(payload["recall_plan"]["depth"], "session")
        self.assertIn("read_episodic_summary", payload["recall_plan"]["recommended_tools"])
        self.assertGreaterEqual(len(payload["results"]), 1)

    def test_recall_planner_depths(self) -> None:
        period = plan_episodic_recall("everything about my knee injury")
        self.assertEqual(period["depth"], "period")

        turns = plan_episodic_recall("what exactly did I say about pasta")
        self.assertEqual(turns["depth"], "turns")
        self.assertIn("read_episodic_turns", turns["recommended_tools"])

        dated = plan_episodic_recall("on 2026-07-05 what did we talk about")
        self.assertEqual(dated["depth"], "turns")

    def test_persona_isolation(self) -> None:
        payload = search_episodic_memory(self.memory_root, "coach", query="telegram bug")
        self.assertEqual(payload["results"], [])

    def test_rebuild_episodic_index(self) -> None:
        index_vectors = index_dir(self.memory_root, "buddy") / "vectors.json"
        index_vectors.unlink()
        count = rebuild_episodic_index(self.memory_root, "buddy", embed_fn=_mock_embed)
        self.assertGreaterEqual(count, 3)
        self.assertTrue(index_vectors.is_file())

    def test_registry_includes_search_tool(self) -> None:
        names = {tool.name for tool in ALL_TOOL_DEFINITIONS}
        self.assertIn("search_episodic_memory", names)
        self.assertIn("search_episodic_memory", EPISODIC_TOOL_NAMES)

    def test_execute_episodic_tool_search(self) -> None:
        result = execute_episodic_tool(
            self.memory_root,
            "buddy",
            "search_episodic_memory",
            {"query": "weather in Boston"},
        )
        self.assertNotIn("Error", result.output)
        payload = json.loads(result.output)
        self.assertIn("results", payload)
        self.assertTrue(
            any(hit.get("session_id") == self.session_ids["day1_id"] for hit in payload["results"])
        )

    def test_execute_tool_search_via_registry(self) -> None:
        result = execute_tool(
            self.memory_root,
            "search_episodic_memory",
            json.dumps({"query": "pasta recipes"}),
            persona_namespace="buddy",
        )
        payload = json.loads(result.output)
        self.assertNotIn("Error", result.output)
        self.assertTrue(
            any(hit.get("session_id") == self.session_ids["day2_id"] for hit in payload["results"])
        )


if __name__ == "__main__":
    unittest.main()
