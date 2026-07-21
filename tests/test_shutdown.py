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
from buddy_tools.infra.bootstrap import configure_runtime_tools, set_memory_root
from buddy_tools.infra.data_dir import reset_data_dir_config
from buddy_tools.infra.shutdown import finalize_buddy_session, reset_shutdown_state_for_tests
from buddy_tools.personality import create_personality, set_active_personality, set_personalities_dir
from buddy_tools.pulse.state import PulseState, load_pulse_state, pulse_state_path, save_pulse_state
from buddy_tools.skills import SkillState, load_skill_state, save_skill_state, teardown_persisted_skill_session
from buddy_tools.voice.voices import set_voices_dir
from speech_to_speech.api.openai_realtime.runtime_config import RuntimeConfig
from speech_to_speech.utils.thread_manager import ThreadManager


class SkillSessionCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.memory_root = self.root / "memory"
        self.personalities_root = self.root / "personalities"
        self.voices_root = self.root / "voices"
        self.memory_root.mkdir()
        self.personalities_root.mkdir()
        self.voices_root.mkdir()

        reset_data_dir_config(repo_root=self.root / "repo", data_dir=self.root / "data")
        set_personalities_dir(self.personalities_root)
        set_voices_dir(self.voices_root)
        set_memory_root(self.memory_root)
        reset_shutdown_state_for_tests()

        voice_dir = self.voices_root / "cliff"
        voice_dir.mkdir()
        (voice_dir / "audio.wav").write_bytes(b"RIFF")
        (voice_dir / "ref_text.txt").write_text("cliff transcript", encoding="utf-8")
        create_personality("buddy", "Buddy", "You are Buddy.", voice_id="cliff")
        set_active_personality("buddy")

    def tearDown(self) -> None:
        reset_data_dir_config()
        reset_shutdown_state_for_tests()
        self._tmpdir.cleanup()

    def test_finalize_clears_checklist_skill_state(self) -> None:
        save_skill_state(
            self.memory_root,
            "buddy",
            SkillState(
                skill_name="equipment-setup",
                status="in_progress",
                step_index=1,
                skill_type="checklist",
            ),
        )

        finalize_buddy_session()

        self.assertIsNone(load_skill_state(self.memory_root, "buddy"))

    def test_finalize_clears_pulse_skill_state(self) -> None:
        save_skill_state(
            self.memory_root,
            "buddy",
            SkillState(
                skill_name="live-director",
                status="in_progress",
                step_index=0,
                skill_type="pulse",
            ),
        )
        save_pulse_state(
            self.memory_root,
            "buddy",
            PulseState(skill_name="live-director", status="active", phase="live"),
        )

        finalize_buddy_session()

        self.assertIsNone(load_skill_state(self.memory_root, "buddy"))
        self.assertIsNone(load_pulse_state(self.memory_root, "buddy"))
        self.assertFalse(pulse_state_path(self.memory_root, "buddy").is_file())

    def test_startup_clears_stale_skill_state(self) -> None:
        save_skill_state(
            self.memory_root,
            "buddy",
            SkillState(
                skill_name="equipment-setup",
                status="in_progress",
                step_index=0,
                skill_type="checklist",
            ),
        )

        configure_runtime_tools(RuntimeConfig(), self.memory_root)

        self.assertIsNone(load_skill_state(self.memory_root, "buddy"))

    def test_teardown_clears_orphaned_pulse_state(self) -> None:
        save_pulse_state(
            self.memory_root,
            "buddy",
            PulseState(skill_name="live-director", status="active", phase="live"),
        )

        skill_name = teardown_persisted_skill_session(
            self.memory_root,
            "buddy",
            reason="startup",
        )

        self.assertEqual(skill_name, "live-director")
        self.assertIsNone(load_pulse_state(self.memory_root, "buddy"))


class ThreadManagerShutdownPatchTests(unittest.TestCase):
    def test_wait_uses_timed_join(self) -> None:
        from buddy_tools.core.patch import _patch_thread_manager_shutdown

        _patch_thread_manager_shutdown()
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
        from buddy_tools.core.patch import _patch_thread_manager_shutdown

        _patch_thread_manager_shutdown()

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
