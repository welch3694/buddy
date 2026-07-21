"""Tests for episodic memory Phase 4 — retrieval tools."""

from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from buddy_tools.episodic.config import EpisodicConfig
from buddy_tools.episodic.consolidation import consolidate_session
from buddy_tools.episodic.manager import EpisodicSessionManager, reset_episodic_for_tests
from buddy_tools.episodic.paths import bucket_keys
from buddy_tools.episodic.retrieval import (
    EPISODIC_TOOL_NAMES,
    execute_episodic_tool,
    find_episodes_by_topic,
    list_episodic_periods,
    read_episodic_summary,
    read_episodic_turns,
)
from buddy_tools.episodic.turns import EpisodicTurnRecord
from buddy_tools.core.registry import ALL_TOOL_DEFINITIONS, build_tool_instructions, execute_tool


def _mock_llm(system: str, user: str) -> str:
    system_lower = system.lower()
    if "summarize conversation" in system_lower:
        if "pasta" in user.lower() or "cooking" in user.lower():
            return json.dumps(
                {"summary": "User asked about pasta recipes.", "topics": ["cooking", "pasta"]}
            )
        return json.dumps(
            {"summary": "User asked about the weather in Boston.", "topics": ["weather", "boston"]}
        )
    if "merge child summaries" in system_lower or "merge these" in user.lower():
        return json.dumps({"summary": "Consolidated rollup summary."})
    if "durable facts" in system_lower:
        return json.dumps({"facts": []})
    raise AssertionError(f"Unexpected LLM prompt: {system[:80]!r}")


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
    enqueue_consolidation: bool = False,
) -> tuple[Path, str]:
    manager.on_user_activity("voice")
    session = manager.current_session()
    assert session is not None
    session_dir = manager._session_dir
    assert session_dir is not None
    manager.log_turn(EpisodicTurnRecord(role="user", channel="voice", text=user_text))
    manager.log_turn(EpisodicTurnRecord(role="assistant", channel="voice", text=assistant_text))
    patch_ctx = (
        nullcontext()
        if enqueue_consolidation
        else patch("buddy_tools.episodic.manager.enqueue_session_consolidation")
    )
    with patch_ctx:
        manager.force_close("idle_timeout")
    return session_dir, session.session_id


def _build_consolidated_tree(memory_root: Path) -> dict[str, str]:
    """Open two sessions on different days, consolidate both, return session ids."""
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
    consolidate_session(day1_dir, memory_root, "buddy", llm_fn=_mock_llm)

    clock.advance(timedelta(days=1))
    day2_dir, day2_id = _close_session_with_turns(
        manager,
        user_text="Any good pasta recipes?",
        assistant_text="Try cacio e pepe.",
    )
    consolidate_session(day2_dir, memory_root, "buddy", llm_fn=_mock_llm)

    return {"day1_id": day1_id, "day2_id": day2_id}


class EpisodicRetrievalTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.memory_root = Path(self._tmpdir.name) / "memory"
        self.memory_root.mkdir()
        reset_episodic_for_tests()
        self.session_ids = _build_consolidated_tree(self.memory_root)
        self.tz = ZoneInfo("America/New_York")
        self.clock = FakeClock(datetime(2026, 7, 5, 15, 0, 0, tzinfo=UTC))
        self.year, self.year_month, self.day1 = bucket_keys(self.clock.now(), self.tz)
        self.day2 = "2026-07-06"

    def tearDown(self) -> None:
        reset_episodic_for_tests()
        self._tmpdir.cleanup()

    def test_list_episodic_periods_hierarchy(self) -> None:
        root = list_episodic_periods(self.memory_root, "buddy", parent="root")
        self.assertEqual(root["parent"], "root")
        self.assertEqual([entry["id"] for entry in root["entries"]], [self.year])

        months = list_episodic_periods(
            self.memory_root,
            "buddy",
            parent="year",
            year=self.year,
        )
        self.assertEqual([entry["id"] for entry in months["entries"]], [self.year_month])

        days = list_episodic_periods(
            self.memory_root,
            "buddy",
            parent="month",
            year=self.year,
            month=self.year_month,
        )
        day_ids = [entry["id"] for entry in days["entries"]]
        self.assertIn(self.day1, day_ids)
        self.assertIn(self.day2, day_ids)

        sessions = list_episodic_periods(
            self.memory_root,
            "buddy",
            parent="day",
            year=self.year,
            month=self.year_month,
            date=self.day1,
        )
        self.assertEqual(len(sessions["entries"]), 1)
        entry = sessions["entries"][0]
        self.assertEqual(entry["id"], self.session_ids["day1_id"])
        self.assertIn("blurb", entry)
        self.assertIn("weather", entry["blurb"].lower())
        self.assertNotIn("summary", entry)
        self.assertIn("provenance", entry)

    def test_list_episodic_periods_month_derives_year(self) -> None:
        days = list_episodic_periods(
            self.memory_root,
            "buddy",
            parent="month",
            month=self.year_month,
        )
        self.assertEqual(days["year"], self.year)
        self.assertEqual(days["month"], self.year_month)

    def test_read_episodic_summary_relative_date(self) -> None:
        with patch(
            "buddy_tools.episodic.retrieval.resolve_episodic_date_now",
            return_value=self.day1,
        ):
            day_payload = read_episodic_summary(
                self.memory_root,
                "buddy",
                level="day",
                date="yesterday",
            )
        self.assertEqual(day_payload["summary"]["date"], self.day1)

    def test_read_episodic_summary_day_with_date_only(self) -> None:
        day_payload = read_episodic_summary(
            self.memory_root,
            "buddy",
            level="day",
            date=self.day1,
        )
        self.assertEqual(day_payload["summary"]["date"], self.day1)

    def test_list_episodic_periods_day_with_relative_date(self) -> None:
        with patch(
            "buddy_tools.episodic.retrieval.resolve_episodic_date_now",
            return_value=self.day1,
        ):
            sessions = list_episodic_periods(
                self.memory_root,
                "buddy",
                parent="day",
                date="today",
            )
        self.assertEqual(sessions["date"], self.day1)
        self.assertEqual(sessions["year"], self.year)
        self.assertEqual(sessions["month"], self.year_month)
        self.assertEqual(len(sessions["entries"]), 1)
        self.assertEqual(sessions["entries"][0]["id"], self.session_ids["day1_id"])

    def test_read_episodic_summary_session_defaults_to_today_latest(self) -> None:
        with patch(
            "buddy_tools.episodic.retrieval.resolve_episodic_date_now",
            return_value=self.day1,
        ):
            payload = read_episodic_summary(self.memory_root, "buddy", level="session")
        self.assertEqual(payload["level"], "session")
        self.assertEqual(payload["resolved_date"], self.day1)
        self.assertEqual(payload["selection"], "latest")
        self.assertEqual(payload["summary"]["session_id"], self.session_ids["day1_id"])
        self.assertEqual(len(payload["siblings"]), 1)
        self.assertEqual(payload["siblings"][0]["id"], self.session_ids["day1_id"])

    def test_read_episodic_summary_session_with_date(self) -> None:
        payload = read_episodic_summary(
            self.memory_root,
            "buddy",
            level="session",
            date=self.day2,
        )
        self.assertEqual(payload["resolved_date"], self.day2)
        self.assertEqual(payload["summary"]["session_id"], self.session_ids["day2_id"])
        self.assertIn("pasta", payload["summary"]["summary"].lower())

    def test_read_episodic_summary_session_prefers_closed_over_open(self) -> None:
        clock = FakeClock(datetime(2026, 7, 6, 18, 0, 0, tzinfo=UTC))
        manager = EpisodicSessionManager(
            self.memory_root,
            "buddy",
            config=_test_config(),
            now_fn=clock.now,
        )
        manager.on_user_activity("voice")
        open_session = manager.current_session()
        assert open_session is not None
        open_id = open_session.session_id

        payload = read_episodic_summary(
            self.memory_root,
            "buddy",
            level="session",
            date=self.day2,
        )
        self.assertEqual(payload["selection"], "latest_closed")
        self.assertEqual(payload["summary"]["session_id"], self.session_ids["day2_id"])
        sibling_ids = {entry["id"] for entry in payload["siblings"]}
        self.assertIn(self.session_ids["day2_id"], sibling_ids)
        self.assertIn(open_id, sibling_ids)
        with patch("buddy_tools.episodic.manager.enqueue_session_consolidation"):
            manager.force_close("idle_timeout")

    def test_date_shaped_session_id_errors(self) -> None:
        with self.assertRaises(ValueError) as summary_err:
            read_episodic_summary(
                self.memory_root,
                "buddy",
                level="session",
                session_id=self.day1,
            )
        self.assertIn("calendar date", str(summary_err.exception))
        self.assertIn("YYYYMMDDTHHMMSS", str(summary_err.exception))

        with self.assertRaises(ValueError) as turns_err:
            read_episodic_turns(self.memory_root, "buddy", session_id=self.day1)
        self.assertIn("calendar date", str(turns_err.exception))
        self.assertIn("list_episodic_periods", str(turns_err.exception))

    def test_read_episodic_summary_each_level(self) -> None:
        year_payload = read_episodic_summary(
            self.memory_root,
            "buddy",
            level="year",
            year=self.year,
        )
        self.assertEqual(year_payload["level"], "year")
        self.assertIn("summary", year_payload)
        self.assertIn("provenance", year_payload)

        month_payload = read_episodic_summary(
            self.memory_root,
            "buddy",
            level="month",
            year=self.year,
            month=self.year_month,
        )
        self.assertEqual(month_payload["summary"]["level"], "month")

        day_payload = read_episodic_summary(
            self.memory_root,
            "buddy",
            level="day",
            year=self.year,
            month=self.year_month,
            date=self.day1,
        )
        self.assertEqual(day_payload["summary"]["date"], self.day1)

        session_payload = read_episodic_summary(
            self.memory_root,
            "buddy",
            level="session",
            session_id=self.session_ids["day1_id"],
        )
        self.assertEqual(session_payload["summary"]["session_id"], self.session_ids["day1_id"])
        self.assertIn("weather", session_payload["summary"]["summary"].lower())
        self.assertEqual(session_payload["provenance"]["session_id"], self.session_ids["day1_id"])

    def test_read_episodic_turns_pagination(self) -> None:
        full = read_episodic_turns(
            self.memory_root,
            "buddy",
            session_id=self.session_ids["day1_id"],
        )
        self.assertEqual(full["total_count"], 2)
        self.assertEqual(len(full["turns"]), 2)
        self.assertFalse(full["has_more"])

        page = read_episodic_turns(
            self.memory_root,
            "buddy",
            session_id=self.session_ids["day1_id"],
            offset=0,
            limit=1,
        )
        self.assertEqual(page["offset"], 0)
        self.assertEqual(page["limit"], 1)
        self.assertEqual(len(page["turns"]), 1)
        self.assertTrue(page["has_more"])

        capped = read_episodic_turns(
            self.memory_root,
            "buddy",
            session_id=self.session_ids["day1_id"],
            limit=50,
        )
        self.assertEqual(capped["limit"], 50)

    def test_find_episodes_by_topic(self) -> None:
        by_tag = find_episodes_by_topic(self.memory_root, "buddy", query="weather")
        self.assertGreaterEqual(len(by_tag["results"]), 1)
        session_hits = [hit for hit in by_tag["results"] if hit["match_type"] == "session"]
        self.assertTrue(any(hit["session_id"] == self.session_ids["day1_id"] for hit in session_hits))

        by_text = find_episodes_by_topic(self.memory_root, "buddy", query="pasta")
        self.assertTrue(
            any(
                hit.get("session_id") == self.session_ids["day2_id"]
                for hit in by_text["results"]
                if hit["match_type"] == "session"
            )
        )

        isolated = find_episodes_by_topic(self.memory_root, "coach", query="weather")
        self.assertEqual(isolated["results"], [])

    def test_registry_includes_episodic_tools(self) -> None:
        names = {tool.name for tool in ALL_TOOL_DEFINITIONS}
        for tool_name in EPISODIC_TOOL_NAMES:
            self.assertIn(tool_name, names)

        result = execute_tool(
            self.memory_root,
            "list_episodic_periods",
            json.dumps({"parent": "root"}),
            persona_namespace="buddy",
        )
        payload = json.loads(result.output)
        self.assertIn("entries", payload)
        self.assertNotIn("Error", result.output)

    def test_build_tool_instructions_mentions_episodic(self) -> None:
        instructions = build_tool_instructions("Base prompt.", "(no memory saved yet)")
        self.assertIn("read_episodic_summary", instructions)
        self.assertIn("yesterday", instructions.lower())
        self.assertIn("level=session", instructions.lower())
        self.assertIn("semantic memory", instructions.lower())

    def test_execute_episodic_tool_errors(self) -> None:
        result = execute_episodic_tool(
            self.memory_root,
            "buddy",
            "read_episodic_summary",
            {"level": "session", "session_id": "missing-session"},
        )
        self.assertIn("Error", result.output)


if __name__ == "__main__":
    unittest.main()
