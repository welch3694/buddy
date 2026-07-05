"""Tests for buddy_tools.pulse — state, worker, and skill lifecycle integration."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from buddy_tools import personality as personality_module
from buddy_tools import voices as voices_module
from buddy_tools.bootstrap import set_memory_root
from buddy_tools.data_dir import reset_data_dir_config
from buddy_tools.personality import create_personality, set_active_personality, set_personalities_dir
from buddy_tools.pulse.state import (
    PulseState,
    clear_pulse_state,
    init_pulse_state_from_skill,
    load_pulse_state,
    pulse_state_path,
    save_pulse_state,
)
from buddy_tools.pulse.worker import (
    get_pulse_worker_manager,
    reset_pulse_workers_for_tests,
    start_pulse_worker,
    stop_pulse_worker,
)
from buddy_tools.registry import build_tool_instructions
from buddy_tools.skills import (
    build_pulse_context,
    execute_skill_tool,
    load_skill_definition,
    load_skill_state,
)
from buddy_tools.voices import set_voices_dir

SAMPLE_PULSE_SKILL = """\
---
name: live-director
description: Timed director flow with camera-switch cues.
metadata:
  buddy:
    type: pulse
---

# Live director

Narrate directed cues from the pulse worker. Do not advance steps manually.
"""


class PulseStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.memory_root = Path(self._tmpdir.name) / "memory"
        self.memory_root.mkdir()

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_save_load_and_clear_pulse_state(self) -> None:
        state = PulseState(skill_name="live-director", status="active", phase="intro")
        save_pulse_state(self.memory_root, "coach", state)
        path = pulse_state_path(self.memory_root, "coach")
        self.assertTrue(path.is_file())

        loaded = load_pulse_state(self.memory_root, "coach")
        assert loaded is not None
        self.assertEqual(loaded.skill_name, "live-director")
        self.assertEqual(loaded.phase, "intro")
        self.assertEqual(loaded.status, "active")

        clear_pulse_state(self.memory_root, "coach")
        self.assertFalse(path.is_file())
        self.assertIsNone(load_pulse_state(self.memory_root, "coach"))

    def test_init_from_session_yaml(self) -> None:
        skill_dir = Path(self._tmpdir.name) / "live-director"
        refs = skill_dir / "references"
        refs.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(SAMPLE_PULSE_SKILL, encoding="utf-8")
        (refs / "session.yaml").write_text(
            "name: live-director\n"
            "pulse:\n"
            "  tick_interval_s: 2\n"
            "init:\n"
            "  set:\n"
            "    phase: warmup\n"
            "cameras:\n"
            "  - cam-a\n"
            "  - cam-b\n"
            "rules: []\n"
            "schedule: []\n",
            encoding="utf-8",
        )

        state = init_pulse_state_from_skill("live-director", skill_dir)
        self.assertEqual(state.skill_name, "live-director")
        self.assertEqual(state.phase, "warmup")
        self.assertEqual(state.tick_interval_seconds, 2.0)
        session = state.get_session_config()
        assert session is not None
        self.assertEqual(list(session.cameras), ["cam-a", "cam-b"])


class PulseWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_pulse_workers_for_tests()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.memory_root = Path(self._tmpdir.name) / "memory"
        self.memory_root.mkdir()

    def tearDown(self) -> None:
        reset_pulse_workers_for_tests()
        self._tmpdir.cleanup()

    def test_worker_ticks_and_stops(self) -> None:
        state = PulseState(
            skill_name="live-director",
            status="active",
            tick_interval_seconds=0.1,
        )
        save_pulse_state(self.memory_root, "coach", state)
        start_pulse_worker(
            self.memory_root,
            "coach",
            "live-director",
            tick_interval_seconds=0.1,
        )

        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            loaded = load_pulse_state(self.memory_root, "coach")
            assert loaded is not None
            if loaded.tick_count >= 1:
                break
            time.sleep(0.05)

        loaded = load_pulse_state(self.memory_root, "coach")
        assert loaded is not None
        self.assertGreaterEqual(loaded.tick_count, 1)
        self.assertIsNotNone(loaded.last_tick_at)

        self.assertTrue(stop_pulse_worker("coach"))
        manager = get_pulse_worker_manager()
        self.assertNotIn("coach", manager._workers)

    def test_worker_does_not_tick_when_paused(self) -> None:
        state = PulseState(
            skill_name="live-director",
            status="paused",
            tick_interval_seconds=0.1,
        )
        save_pulse_state(self.memory_root, "coach", state)
        start_pulse_worker(
            self.memory_root,
            "coach",
            "live-director",
            tick_interval_seconds=0.1,
        )
        time.sleep(0.35)
        loaded = load_pulse_state(self.memory_root, "coach")
        assert loaded is not None
        self.assertEqual(loaded.tick_count, 0)
        stop_pulse_worker("coach")


class PulseSkillLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_pulse_workers_for_tests()
        self._original_personalities_dir = personality_module.get_personalities_dir()
        self._original_voices_dir = voices_module.get_voices_dir()
        from buddy_tools.bootstrap import get_memory_root

        self._original_memory_root = get_memory_root()

        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.repo_root = self.root / "repo"
        self.personalities_root = self.root / "personalities"
        self.voices_root = self.root / "voices"
        self.memory_root = self.root / "memory"
        self.repo_root.mkdir()
        (self.repo_root / "skills").mkdir()
        self.personalities_root.mkdir()
        self.voices_root.mkdir()
        self.memory_root.mkdir()

        reset_data_dir_config(repo_root=self.repo_root, data_dir=self.root / "data")
        set_personalities_dir(self.personalities_root)
        set_voices_dir(self.voices_root)
        set_memory_root(self.memory_root)

        self._write_voice("cliff")
        create_personality("buddy", "Buddy", "You are Buddy.", voice_id="cliff")
        create_personality("coach", "Coach", "You are Coach.", voice_id="cliff")
        self._write_pulse_skill("coach", "live-director")

    def tearDown(self) -> None:
        reset_pulse_workers_for_tests()
        reset_data_dir_config()
        set_personalities_dir(self._original_personalities_dir)
        set_voices_dir(self._original_voices_dir)
        if self._original_memory_root is not None:
            set_memory_root(self._original_memory_root)
        self._tmpdir.cleanup()

    def _write_voice(self, voice_id: str) -> None:
        voice_dir = self.voices_root / voice_id
        voice_dir.mkdir(parents=True, exist_ok=True)
        (voice_dir / "audio.wav").write_bytes(b"RIFF")
        (voice_dir / "ref_text.txt").write_text(f"{voice_id} transcript", encoding="utf-8")

    def _write_pulse_skill(self, personality_id: str, skill_name: str) -> None:
        skill_dir = self.personalities_root / personality_id / "skills" / skill_name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(SAMPLE_PULSE_SKILL, encoding="utf-8")
        refs = skill_dir / "references"
        refs.mkdir()
        (refs / "session.yaml").write_text(
            "name: live-director\n"
            "pulse:\n"
            "  tick_interval_s: 0.15\n"
            "init:\n"
            "  set:\n"
            "    phase: intro\n"
            "rules: []\n"
            "schedule: []\n",
            encoding="utf-8",
        )

    def test_load_skill_definition_recognizes_pulse_type(self) -> None:
        skill_dir = self.personalities_root / "coach" / "skills" / "live-director"
        skill = load_skill_definition(skill_dir)
        self.assertEqual(skill.skill_type, "pulse")

    def test_start_and_cancel_pulse_skill(self) -> None:
        set_active_personality("coach")

        started = execute_skill_tool(
            self.memory_root, "coach", "start_skill", {"name": "live-director"}
        )
        self.assertNotIn("Error", started.output)
        self.assertIn("pulse session", started.output.lower())

        skill_state = load_skill_state(self.memory_root, "coach")
        assert skill_state is not None
        self.assertEqual(skill_state.skill_type, "pulse")

        pulse_path = pulse_state_path(self.memory_root, "coach")
        self.assertTrue(pulse_path.is_file())
        pulse = load_pulse_state(self.memory_root, "coach")
        assert pulse is not None
        self.assertEqual(pulse.phase, "intro")

        manager = get_pulse_worker_manager()
        self.assertIn("coach", manager._workers)

        cancelled = execute_skill_tool(self.memory_root, "coach", "cancel_skill", {})
        self.assertNotIn("Error", cancelled.output)
        self.assertIsNone(load_skill_state(self.memory_root, "coach"))
        self.assertFalse(pulse_path.is_file())
        self.assertNotIn("coach", manager._workers)

    def test_persona_isolation(self) -> None:
        self._write_pulse_skill("buddy", "live-director")

        set_active_personality("coach")
        execute_skill_tool(self.memory_root, "coach", "start_skill", {"name": "live-director"})

        set_active_personality("buddy")
        execute_skill_tool(self.memory_root, "buddy", "start_skill", {"name": "live-director"})

        coach_pulse = load_pulse_state(self.memory_root, "coach")
        buddy_pulse = load_pulse_state(self.memory_root, "buddy")
        assert coach_pulse is not None
        assert buddy_pulse is not None
        self.assertEqual(coach_pulse.skill_name, "live-director")
        self.assertEqual(buddy_pulse.skill_name, "live-director")

        manager = get_pulse_worker_manager()
        self.assertIn("coach", manager._workers)
        self.assertIn("buddy", manager._workers)

        execute_skill_tool(self.memory_root, "coach", "cancel_skill", {})
        self.assertIsNone(load_pulse_state(self.memory_root, "coach"))
        self.assertIsNotNone(load_pulse_state(self.memory_root, "buddy"))

    def test_build_pulse_context_in_instructions(self) -> None:
        from buddy_tools.personality import get_personality

        set_active_personality("coach")
        execute_skill_tool(self.memory_root, "coach", "start_skill", {"name": "live-director"})

        profile = get_personality("coach")
        context = build_pulse_context(self.memory_root, "coach", profile)
        self.assertIn("Active pulse session", context)
        self.assertIn("live-director", context)
        self.assertIn("intro", context)

        instructions = build_tool_instructions(
            "Base prompt.",
            "Memory snapshot.",
            memory_root=self.memory_root,
            persona_namespace="coach",
            personality_id="coach",
        )
        self.assertIn("Active pulse session", instructions)

    def test_only_one_active_pulse_per_persona(self) -> None:
        set_active_personality("coach")
        execute_skill_tool(self.memory_root, "coach", "start_skill", {"name": "live-director"})

        skill_dir = self.personalities_root / "coach" / "skills" / "interval-coach"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            SAMPLE_PULSE_SKILL.replace("live-director", "interval-coach"),
            encoding="utf-8",
        )
        (skill_dir / "references").mkdir()
        (skill_dir / "references" / "session.yaml").write_text(
            "name: interval-coach\n"
            "init:\n"
            "  set:\n"
            "    phase: core\n"
            "rules: []\n"
            "schedule: []\n",
            encoding="utf-8",
        )

        execute_skill_tool(self.memory_root, "coach", "start_skill", {"name": "interval-coach"})

        skill_state = load_skill_state(self.memory_root, "coach")
        assert skill_state is not None
        self.assertEqual(skill_state.skill_name, "interval-coach")

        pulse = load_pulse_state(self.memory_root, "coach")
        assert pulse is not None
        self.assertEqual(pulse.skill_name, "interval-coach")
        self.assertEqual(pulse.phase, "core")

        manager = get_pulse_worker_manager()
        self.assertEqual(len(manager._workers), 1)
        worker = manager._workers["coach"]
        self.assertEqual(worker.skill_name, "interval-coach")


if __name__ == "__main__":
    unittest.main()
