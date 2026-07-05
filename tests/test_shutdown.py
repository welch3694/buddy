"""Tests for graceful shutdown (Ctrl+C fix and episodic session cleanup)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from queue import Queue
from threading import Event, Thread

from buddy_tools.core.executor import LocalToolExecutor
from buddy_tools.episodic import EpisodicTurnRecord, configure_episodic, get_episodic_manager, reset_episodic_for_tests
from buddy_tools.episodic.session import load_session
from buddy_tools.infra.shutdown import finalize_buddy_session, reset_shutdown_state_for_tests
from speech_to_speech.utils.thread_manager import ThreadManager


class ThreadManagerShutdownPatchTests(unittest.TestCase):
    def test_wait_uses_timed_join(self) -> None:
        from buddy_tools.core.patch import apply_patches

        apply_patches()
        source = ThreadManager.wait.__doc__ or ""
        self.assertIn("SIGINT", source)


class ExecutorCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.memory_root = Path(self._tmpdir.name) / "memory"
        self.memory_root.mkdir()
        reset_episodic_for_tests()
        reset_shutdown_state_for_tests()
        configure_episodic(self.memory_root, "buddy")

    def tearDown(self) -> None:
        reset_episodic_for_tests()
        reset_shutdown_state_for_tests()
        self._tmpdir.cleanup()

    def test_cleanup_closes_episodic_session(self) -> None:
        manager = get_episodic_manager()
        assert manager is not None
        manager.on_user_activity("voice")
        manager.log_turn(
            EpisodicTurnRecord(role="user", channel="voice", turn_id="v1", text="Hi")
        )
        session_id = manager.current_session().session_id  # type: ignore[union-attr]

        executor = LocalToolExecutor(Event(), Queue(), Queue())
        executor.cleanup()

        session_path = next(self.memory_root.rglob("session.json"))
        closed = load_session(session_path)
        assert closed is not None
        self.assertEqual(closed.session_id, session_id)
        self.assertEqual(closed.status, "close_pending")
        self.assertEqual(closed.idle_reason, "shutdown")

    def test_cleanup_is_idempotent(self) -> None:
        manager = get_episodic_manager()
        assert manager is not None
        manager.on_user_activity("voice")

        executor = LocalToolExecutor(Event(), Queue(), Queue())
        executor.cleanup()
        executor.cleanup()


class FinalizeBuddySessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.memory_root = Path(self._tmpdir.name) / "memory"
        self.memory_root.mkdir()
        reset_episodic_for_tests()
        reset_shutdown_state_for_tests()
        configure_episodic(self.memory_root, "buddy")

    def tearDown(self) -> None:
        reset_episodic_for_tests()
        reset_shutdown_state_for_tests()
        self._tmpdir.cleanup()

    def test_finalize_closes_open_session(self) -> None:
        manager = get_episodic_manager()
        assert manager is not None
        manager.on_user_activity("voice")
        finalize_buddy_session()
        self.assertIsNone(manager.current_session())

    def test_finalize_is_idempotent(self) -> None:
        manager = get_episodic_manager()
        assert manager is not None
        manager.on_user_activity("voice")
        finalize_buddy_session()
        finalize_buddy_session()


class TimedJoinAllowsStopTests(unittest.TestCase):
    def test_timed_join_lets_stop_event_unblock_wait(self) -> None:
        from buddy_tools.core.patch import apply_patches

        apply_patches()

        stop_event = Event()

        class SlowHandler:
            def __init__(self) -> None:
                self.stop_event = stop_event

            def run(self) -> None:
                while not self.stop_event.is_set():
                    stop_event.wait(0.05)

        handler = SlowHandler()
        manager = ThreadManager([handler])
        manager.start()

        def stop_soon() -> None:
            stop_event.set()

        Thread(target=stop_soon, daemon=True).start()
        manager.wait()
        self.assertFalse(any(thread.is_alive() for thread in manager.threads))


if __name__ == "__main__":
    unittest.main()
