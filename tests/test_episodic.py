"""Tests for episodic memory Phase 1 — storage layout and session manager."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from buddy_tools.episodic.config import (
    EpisodicConfig,
    load_episodic_config,
    reset_episodic_config_for_tests,
)
from buddy_tools.episodic.manager import EpisodicSessionManager, reset_episodic_for_tests
from buddy_tools.episodic.paths import (
    SESSION_FILENAME,
    TURNS_FILENAME,
    bucket_keys,
    day_dir,
    episodic_root,
    session_dir,
    session_id_for,
)
from buddy_tools.episodic.session import EpisodicSession, load_session, save_session


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self._current = start

    def now(self) -> datetime:
        return self._current

    def advance(self, *, seconds: float = 0, minutes: float = 0) -> None:
        delta = timedelta(seconds=seconds, minutes=minutes)
        self._current = self._current + delta


def _make_manager(
    memory_root: Path,
    persona: str = "buddy",
    *,
    clock: FakeClock,
    agent_busy: bool = False,
    idle_minutes: int = 20,
    max_minutes: int = 120,
) -> EpisodicSessionManager:
    config = EpisodicConfig(
        idle_timeout_minutes=idle_minutes,
        max_session_minutes=max_minutes,
        timezone="America/New_York",
    )
    return EpisodicSessionManager(
        memory_root,
        persona,
        config=config,
        agent_busy_fn=lambda: agent_busy,
        now_fn=clock.now,
    )


class EpisodicPathsTests(unittest.TestCase):
    def test_bucket_keys_eastern(self) -> None:
        tz = ZoneInfo("America/New_York")
        # 2026-07-05 02:30 UTC = 2026-07-04 22:30 ET (still previous day)
        utc = datetime(2026, 7, 5, 2, 30, tzinfo=UTC)
        year, month, day = bucket_keys(utc, tz)
        self.assertEqual(year, "2026")
        self.assertEqual(month, "2026-07")
        self.assertEqual(day, "2026-07-04")

    def test_session_id_sortable_prefix(self) -> None:
        tz = ZoneInfo("America/New_York")
        utc = datetime(2026, 7, 5, 18, 0, 0, tzinfo=UTC)
        session_id = session_id_for(utc, tz)
        self.assertTrue(session_id.startswith("20260705T"))
        self.assertIn("-", session_id)


class EpisodicSessionManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.memory_root = Path(self._tmpdir.name) / "memory"
        self.memory_root.mkdir()
        self.clock = FakeClock(datetime(2026, 7, 5, 15, 0, 0, tzinfo=UTC))
        reset_episodic_for_tests()

    def tearDown(self) -> None:
        reset_episodic_for_tests()
        self._tmpdir.cleanup()

    def test_no_episodic_dirs_before_first_activity(self) -> None:
        _make_manager(self.memory_root, clock=self.clock)
        root = episodic_root(self.memory_root, "buddy")
        self.assertFalse(root.exists())

    def test_path_layout_after_open(self) -> None:
        manager = _make_manager(self.memory_root, clock=self.clock)
        manager.on_user_activity("voice")

        session = manager.current_session()
        assert session is not None
        tz = ZoneInfo("America/New_York")
        year, year_month, year_month_day = bucket_keys(self.clock.now(), tz)
        directory = session_dir(
            self.memory_root, "buddy", year, year_month, year_month_day, session.session_id
        )

        self.assertTrue((directory / SESSION_FILENAME).is_file())
        self.assertTrue((directory / TURNS_FILENAME).is_file())
        self.assertTrue((day_dir(self.memory_root, "buddy", year, year_month, year_month_day) / "day.json").is_file())
        self.assertTrue(
            (self.memory_root / "buddy" / "episodic" / year / year_month / "month.json").is_file()
        )
        self.assertTrue((self.memory_root / "buddy" / "episodic" / year / "year.json").is_file())

        loaded = load_session(directory / SESSION_FILENAME)
        assert loaded is not None
        self.assertEqual(loaded.status, "open")
        self.assertEqual(loaded.channels, ["voice"])
        self.assertEqual(loaded.turn_count, 0)
        self.assertEqual(loaded.summary, "")
        self.assertEqual(loaded.topics, [])

    def test_status_transitions_and_idempotent_close(self) -> None:
        manager = _make_manager(self.memory_root, clock=self.clock)
        manager.on_user_activity("voice")
        session_id = manager.current_session().session_id  # type: ignore[union-attr]

        self.assertTrue(manager.force_close("idle_timeout"))
        path = self._find_session_path(session_id)
        closed = load_session(path)
        assert closed is not None
        self.assertEqual(closed.status, "closed")
        self.assertEqual(closed.idle_reason, "idle_timeout")
        self.assertIsNotNone(closed.ended_at)

        self.assertFalse(manager.force_close("idle_timeout"))
        self.assertIsNone(manager.current_session())

    def test_idle_timer_closes_session(self) -> None:
        manager = _make_manager(self.memory_root, clock=self.clock, idle_minutes=20)
        manager.on_user_activity("voice")
        session_id = manager.current_session().session_id  # type: ignore[union-attr]

        with patch.object(manager, "_arm_idle_timer") as mock_arm:
            manager.on_user_activity("voice")
            mock_arm.assert_called()

        manager._cancel_idle_timer()
        self.clock.advance(minutes=21)
        self.assertTrue(manager.close_if_idle())

        closed = load_session(self._find_session_path(session_id))
        assert closed is not None
        self.assertEqual(closed.status, "closed")
        self.assertEqual(closed.idle_reason, "idle_timeout")

    def test_agent_busy_defers_idle_close(self) -> None:
        busy = {"value": True}

        manager = EpisodicSessionManager(
            self.memory_root,
            "buddy",
            config=EpisodicConfig(1, 120, "America/New_York"),
            agent_busy_fn=lambda: busy["value"],
            now_fn=self.clock.now,
        )
        manager.on_user_activity("voice")
        session_id = manager.current_session().session_id  # type: ignore[union-attr]
        self.assertFalse(manager.close_if_idle())
        self.assertEqual(manager.current_session().status, "open")  # type: ignore[union-attr]

        busy["value"] = False
        self.assertTrue(manager.close_if_idle())
        closed = load_session(self._find_session_path(session_id))
        assert closed is not None
        self.assertEqual(closed.idle_reason, "idle_timeout")

    def test_max_duration_split(self) -> None:
        manager = _make_manager(
            self.memory_root,
            clock=self.clock,
            max_minutes=120,
        )
        manager.on_user_activity("voice")
        first = manager.current_session()
        assert first is not None
        first_id = first.session_id

        self.clock.advance(minutes=121)
        second = manager.on_user_activity("telegram")
        assert second is not None
        self.assertNotEqual(second.session_id, first_id)
        self.assertIn("voice", load_session(self._find_session_path(first_id)).channels)  # type: ignore[union-attr]
        self.assertIn("telegram", second.channels)

        first_closed = load_session(self._find_session_path(first_id))
        assert first_closed is not None
        self.assertEqual(first_closed.status, "closed")
        self.assertEqual(first_closed.idle_reason, "max_duration")

    def test_shutdown_force_close(self) -> None:
        manager = _make_manager(self.memory_root, clock=self.clock)
        manager.on_user_activity("voice")
        session_id = manager.current_session().session_id  # type: ignore[union-attr]
        self.assertTrue(manager.force_close("shutdown"))

        closed = load_session(self._find_session_path(session_id))
        assert closed is not None
        self.assertEqual(closed.status, "closed")
        self.assertEqual(closed.idle_reason, "shutdown")

    def test_channel_tracking(self) -> None:
        manager = _make_manager(self.memory_root, clock=self.clock)
        manager.on_user_activity("voice")
        manager.on_user_activity("telegram")

        session = manager.current_session()
        assert session is not None
        self.assertEqual(session.channels, ["voice", "telegram"])

    def test_orphan_recovery_on_init(self) -> None:
        tz = ZoneInfo("America/New_York")
        year, year_month, year_month_day = bucket_keys(self.clock.now(), tz)
        directory = session_dir(
            self.memory_root,
            "buddy",
            year,
            year_month,
            year_month_day,
            "20260705T150000-deadbeef",
        )
        directory.mkdir(parents=True)
        orphan = EpisodicSession(
            session_id="20260705T150000-deadbeef",
            status="open",
            started_at=self.clock.now().isoformat(),
            persona_namespace="buddy",
            channels=["voice"],
        )
        save_session(directory / SESSION_FILENAME, orphan)

        _make_manager(self.memory_root, clock=self.clock)
        recovered = load_session(directory / SESSION_FILENAME)
        assert recovered is not None
        self.assertEqual(recovered.status, "closed")
        self.assertEqual(recovered.idle_reason, "shutdown")

    def _find_session_path(self, session_id: str) -> Path:
        for path in episodic_root(self.memory_root, "buddy").rglob(SESSION_FILENAME):
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("session_id") == session_id:
                return path
        raise AssertionError(f"session {session_id!r} not found")


class EpisodicConfigTests(unittest.TestCase):
    def tearDown(self) -> None:
        reset_episodic_config_for_tests()
        for key in (
            "BUDDY_EPISODIC_IDLE_TIMEOUT_MINUTES",
            "BUDDY_EPISODIC_MAX_SESSION_MINUTES",
            "BUDDY_EPISODIC_TIMEZONE",
        ):
            os.environ.pop(key, None)

    def test_defaults(self) -> None:
        config = load_episodic_config(force=True)
        self.assertEqual(config.idle_timeout_minutes, 20)
        self.assertEqual(config.max_session_minutes, 120)
        self.assertEqual(config.timezone, "America/New_York")

    def test_env_overrides(self) -> None:
        os.environ["BUDDY_EPISODIC_IDLE_TIMEOUT_MINUTES"] = "5"
        os.environ["BUDDY_EPISODIC_MAX_SESSION_MINUTES"] = "30"
        os.environ["BUDDY_EPISODIC_TIMEZONE"] = "UTC"
        config = load_episodic_config(force=True)
        self.assertEqual(config.idle_timeout_minutes, 5)
        self.assertEqual(config.max_session_minutes, 30)
        self.assertEqual(config.timezone, "UTC")


if __name__ == "__main__":
    unittest.main()
