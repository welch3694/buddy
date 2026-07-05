"""Tests for episodic memory Phase 3 — consolidation and semantic fact extraction."""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from buddy_tools.episodic.config import EpisodicConfig
from buddy_tools.episodic.consolidation import (
    consolidate_session,
    format_turns_for_prompt,
    summarize_session,
)
from buddy_tools.episodic.manager import EpisodicSessionManager, reset_episodic_for_tests
from buddy_tools.episodic.paths import (
    bucket_keys,
    day_rollup_path,
    month_rollup_path,
    session_json_path,
    turns_jsonl_path,
    year_rollup_path,
)
from buddy_tools.episodic.regenerate import find_session_directory, regenerate_session
from buddy_tools.episodic.session import load_session
from buddy_tools.episodic.turns import EpisodicTurnRecord, load_turns
from buddy_tools.episodic.worker import (
    configure_consolidation_worker,
    get_consolidation_worker,
    reset_consolidation_worker_for_tests,
)
from buddy_tools.memory import global_memory_dir, persona_memory_dir


def _mock_llm(system: str, user: str) -> str:
    system_lower = system.lower()
    if "summarize conversation" in system_lower:
        return json.dumps(
            {"summary": "User asked about the weather in Boston.", "topics": ["weather", "boston"]}
        )
    if "merge child summaries" in system_lower or "merge these" in user.lower():
        return json.dumps({"summary": "Consolidated rollup summary."})
    if "durable facts" in system_lower:
        return json.dumps(
            {
                "facts": [
                    {
                        "scope": "global",
                        "name": "notes",
                        "topic": "City",
                        "value": "Boston",
                    },
                    {
                        "scope": "persona",
                        "name": "notes",
                        "topic": "Last topic",
                        "value": "Weather",
                    },
                ]
            }
        )
    raise AssertionError(f"Unexpected LLM prompt: {system[:80]!r}")


def _failing_llm(_system: str, _user: str) -> str:
    raise RuntimeError("LLM unavailable")


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self._current = start

    def now(self) -> datetime:
        return self._current


def _test_config() -> EpisodicConfig:
    return EpisodicConfig(
        idle_timeout_minutes=20,
        max_session_minutes=120,
        timezone="America/New_York",
        consolidation_delay_seconds=0,
        consolidation_retry_base_seconds=1,
    )


def _open_session_with_turns(
    memory_root: Path,
    clock: FakeClock,
    *,
    enqueue_consolidation: bool = False,
) -> tuple[Path, str]:
    manager = EpisodicSessionManager(
        memory_root,
        "buddy",
        config=_test_config(),
        now_fn=clock.now,
    )
    manager.on_user_activity("voice")
    session = manager.current_session()
    assert session is not None
    session_dir = manager._session_dir
    assert session_dir is not None
    manager.log_turn(
        EpisodicTurnRecord(role="user", channel="voice", text="What's the weather in Boston?"),
    )
    manager.log_turn(
        EpisodicTurnRecord(role="assistant", channel="voice", text="It's sunny today."),
    )
    patch_ctx = (
        nullcontext()
        if enqueue_consolidation
        else patch("buddy_tools.episodic.manager.enqueue_session_consolidation")
    )
    with patch_ctx:
        manager.force_close("idle_timeout")
    return session_dir, session.session_id


class ConsolidationPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.memory_root = Path(self._tmpdir.name) / "memory"
        self.memory_root.mkdir()
        self.clock = FakeClock(datetime(2026, 7, 5, 15, 0, 0, tzinfo=UTC))
        reset_episodic_for_tests()
        reset_consolidation_worker_for_tests()

    def tearDown(self) -> None:
        reset_episodic_for_tests()
        reset_consolidation_worker_for_tests()
        self._tmpdir.cleanup()

    def test_format_turns_for_prompt(self) -> None:
        turns = [
            EpisodicTurnRecord(role="user", channel="voice", text="Hi"),
            EpisodicTurnRecord(
                role="tool",
                channel="voice",
                tool_name="read_memory",
                tool_output_preview="notes content",
            ),
        ]
        text = format_turns_for_prompt(turns)
        self.assertIn("User (voice): Hi", text)
        self.assertIn("[tool:read_memory]", text)

    def test_summarize_session_writes_summary_and_topics(self) -> None:
        session_dir, _ = _open_session_with_turns(self.memory_root, self.clock)
        summarize_session(session_dir, llm_fn=_mock_llm)
        session = load_session(session_json_path(session_dir))
        assert session is not None
        self.assertEqual(session.status, "closed")
        self.assertIn("weather", session.summary.lower())
        self.assertEqual(session.topics, ["weather", "boston"])

    def test_consolidate_session_updates_rollups_and_memory(self) -> None:
        session_dir, session_id = _open_session_with_turns(self.memory_root, self.clock)
        tz = ZoneInfo("America/New_York")
        year, year_month, year_month_day = bucket_keys(self.clock.now(), tz)

        self.assertTrue(
            consolidate_session(
                session_dir,
                self.memory_root,
                "buddy",
                llm_fn=_mock_llm,
            )
        )

        session = load_session(session_json_path(session_dir))
        assert session is not None
        self.assertEqual(session.status, "closed")

        day_data = json.loads(
            day_rollup_path(
                self.memory_root, "buddy", year, year_month, year_month_day
            ).read_text(encoding="utf-8")
        )
        self.assertIn("weather", day_data["summary"].lower())

        month_data = json.loads(
            month_rollup_path(self.memory_root, "buddy", year, year_month).read_text(
                encoding="utf-8"
            )
        )
        self.assertTrue(month_data["summary"])

        year_data = json.loads(
            year_rollup_path(self.memory_root, "buddy", year).read_text(encoding="utf-8")
        )
        self.assertTrue(year_data["summary"])

        global_notes = global_memory_dir(self.memory_root) / "notes.md"
        persona_notes = persona_memory_dir(self.memory_root, "buddy") / "notes.md"
        self.assertTrue(global_notes.is_file())
        self.assertIn("Boston", global_notes.read_text(encoding="utf-8"))
        self.assertTrue(persona_notes.is_file())
        self.assertIn("Weather", persona_notes.read_text(encoding="utf-8"))

        found = find_session_directory(self.memory_root, "buddy", session_id)
        self.assertEqual(found, session_dir)

    def test_llm_failure_leaves_turns_and_close_pending(self) -> None:
        session_dir, _ = _open_session_with_turns(self.memory_root, self.clock)
        turns_before = load_turns(turns_jsonl_path(session_dir))

        self.assertFalse(
            consolidate_session(
                session_dir,
                self.memory_root,
                "buddy",
                llm_fn=_failing_llm,
            )
        )

        session = load_session(session_json_path(session_dir))
        assert session is not None
        self.assertEqual(session.status, "close_pending")
        self.assertEqual(session.summary, "")
        self.assertEqual(load_turns(turns_jsonl_path(session_dir)), turns_before)

    def test_regenerate_session_rebuilds_from_turns(self) -> None:
        session_dir, _ = _open_session_with_turns(self.memory_root, self.clock)
        self.assertTrue(
            regenerate_session(
                session_dir,
                self.memory_root,
                "buddy",
                llm_fn=_mock_llm,
            )
        )
        session = load_session(session_json_path(session_dir))
        assert session is not None
        self.assertEqual(session.status, "closed")
        self.assertTrue(session.summary)


class ConsolidationWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.memory_root = Path(self._tmpdir.name) / "memory"
        self.memory_root.mkdir()
        self.clock = FakeClock(datetime(2026, 7, 5, 15, 0, 0, tzinfo=UTC))
        reset_episodic_for_tests()
        reset_consolidation_worker_for_tests()
        configure_consolidation_worker(
            self.memory_root,
            "buddy",
            config=_test_config(),
            llm_fn=_mock_llm,
        )

    def tearDown(self) -> None:
        reset_episodic_for_tests()
        reset_consolidation_worker_for_tests()
        self._tmpdir.cleanup()

    def test_close_enqueues_and_worker_consolidates(self) -> None:
        manager = EpisodicSessionManager(
            self.memory_root,
            "buddy",
            config=_test_config(),
            now_fn=self.clock.now,
        )
        manager.on_user_activity("voice")
        session = manager.current_session()
        assert session is not None
        session_dir = manager._session_dir
        assert session_dir is not None
        manager.log_turn(
            EpisodicTurnRecord(role="user", channel="voice", text="Hello"),
        )
        manager.force_close("idle_timeout")

        pending = load_session(session_json_path(session_dir))
        assert pending is not None
        self.assertEqual(pending.status, "close_pending")

        get_consolidation_worker().process_all_sync(timeout=10.0)

        closed = load_session(session_json_path(session_dir))
        assert closed is not None
        self.assertEqual(closed.status, "closed")
        self.assertTrue(closed.summary)

    def test_reopen_cancels_pending_consolidation(self) -> None:
        config = EpisodicConfig(
            idle_timeout_minutes=20,
            max_session_minutes=120,
            timezone="America/New_York",
            consolidation_delay_seconds=60,
            consolidation_retry_base_seconds=1,
        )
        manager = EpisodicSessionManager(
            self.memory_root,
            "buddy",
            config=config,
            now_fn=self.clock.now,
        )
        manager.on_user_activity("voice")
        session_id = manager.current_session().session_id  # type: ignore[union-attr]
        manager.force_close("idle_timeout")

        worker = get_consolidation_worker()
        self.assertTrue(worker.is_job_pending(session_id))

        reopened = manager.on_user_activity("voice")
        self.assertEqual(reopened.session_id, session_id)
        self.assertEqual(reopened.status, "open")
        self.assertFalse(worker.is_job_pending(session_id))

    def test_worker_processes_one_job_at_a_time(self) -> None:
        active = 0
        max_active = 0
        lock = threading.Lock()

        def counting_llm(system: str, user: str) -> str:
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.05)
            with lock:
                active -= 1
            return _mock_llm(system, user)

        reset_consolidation_worker_for_tests()
        configure_consolidation_worker(
            self.memory_root,
            "buddy",
            config=_test_config(),
            llm_fn=counting_llm,
        )

        for _ in range(2):
            _open_session_with_turns(
                self.memory_root,
                self.clock,
                enqueue_consolidation=True,
            )

        worker = get_consolidation_worker()
        worker.process_all_sync(timeout=15.0)

        self.assertEqual(max_active, 1)


if __name__ == "__main__":
    unittest.main()
