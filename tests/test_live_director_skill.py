"""Tests for the shipped live-director built-in pulse skill."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from buddy_tools import personality as personality_module
import buddy_tools.voice.voices as voices_module
from buddy_tools.infra.bootstrap import set_memory_root
from buddy_tools.infra.data_dir import get_built_in_skills_dir, reset_data_dir_config
from buddy_tools.personality import create_personality, set_active_personality, set_personalities_dir
from buddy_tools.pulse.schema import load_session_config
from buddy_tools.pulse.worker import reset_pulse_workers_for_tests
from buddy_tools.skills import discover_skills, execute_skill_tool, load_skill_state
from buddy_tools.voice.voices import set_voices_dir


class LiveDirectorRepoSkillTests(unittest.TestCase):
    def test_repo_live_director_skill_is_valid_pulse(self) -> None:
        project_root = Path(__file__).resolve().parent.parent
        skill_dir = project_root / "skills" / "live-director"
        from buddy_tools.skills import load_skill_definition

        loaded = load_skill_definition(skill_dir, source="builtin")
        self.assertEqual(loaded.name, "live-director")
        self.assertEqual(loaded.skill_type, "pulse")
        self.assertIn("director", loaded.description.lower())
        self.assertIn("go live", loaded.description.lower())

        body_lower = loaded.body.lower()
        self.assertIn("[no_output]", body_lower)
        self.assertIn("directed", body_lower)
        self.assertIn("call tools", body_lower)

    def test_repo_session_yaml_has_camera_switch_rule(self) -> None:
        project_root = Path(__file__).resolve().parent.parent
        skill_dir = project_root / "skills" / "live-director"
        session = load_session_config(skill_dir, skill_name="live-director")
        self.assertEqual(session.name, "live-director")
        self.assertEqual(session.pulse.tick_interval_s, 10.0)
        self.assertEqual(len(session.cameras), 3)
        self.assertEqual(session.rules[0].id, "camera-switch")
        self.assertIn("switch_interval_s", session.rules[0].when)
        self.assertEqual(session.init_set.get("switch_interval_s"), 180)
        self.assertIn("rotate", str(session.rules[0].set_fields.get("current_camera", "")))

    def test_session_yaml_reference_doc_covers_engine_features(self) -> None:
        project_root = Path(__file__).resolve().parent.parent
        doc = project_root / "buddy_tools" / "pulse" / "SESSION_YAML.md"
        self.assertTrue(doc.is_file(), "missing buddy_tools/pulse/SESSION_YAML.md")
        content = doc.read_text(encoding="utf-8")
        for needle in (
            "session_elapsed",
            "$clamp",
            "$sub",
            "elapsed_since",
            "&&",
            "Limitations",
            "silence_gated_only",
            "keep_them_talking",
        ):
            self.assertIn(needle, content, f"SESSION_YAML.md should document {needle!r}")


class LiveDirectorDiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_pulse_workers_for_tests()
        self._original_personalities_dir = personality_module.get_personalities_dir()
        self._original_voices_dir = voices_module.get_voices_dir()
        from buddy_tools.infra.bootstrap import get_memory_root

        self._original_memory_root = get_memory_root()
        self.project_root = Path(__file__).resolve().parent.parent

        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.personalities_root = self.root / "personalities"
        self.voices_root = self.root / "voices"
        self.memory_root = self.root / "memory"
        self.personalities_root.mkdir()
        self.voices_root.mkdir()
        self.memory_root.mkdir()

        reset_data_dir_config(repo_root=self.project_root, data_dir=self.root / "data")
        set_personalities_dir(self.personalities_root)
        set_voices_dir(self.voices_root)
        set_memory_root(self.memory_root)

        voice_dir = self.voices_root / "cliff"
        voice_dir.mkdir(parents=True)
        (voice_dir / "audio.wav").write_bytes(b"RIFF")
        (voice_dir / "ref_text.txt").write_text("cliff transcript", encoding="utf-8")
        create_personality("coach", "Coach", "You are Coach.", voice_id="cliff")

    def tearDown(self) -> None:
        reset_pulse_workers_for_tests()
        reset_data_dir_config()
        set_personalities_dir(self._original_personalities_dir)
        set_voices_dir(self._original_voices_dir)
        if self._original_memory_root is not None:
            set_memory_root(self._original_memory_root)
        self._tmpdir.cleanup()

    def test_builtin_live_director_discoverable(self) -> None:
        from buddy_tools.personality import get_personality

        profile = get_personality("coach")
        skills = discover_skills(profile)
        by_name = {skill.name: skill for skill in skills}
        self.assertIn("live-director", by_name)
        self.assertEqual(by_name["live-director"].source, "builtin")
        self.assertEqual(by_name["live-director"].skill_type, "pulse")

    def test_built_in_skills_dir_includes_live_director(self) -> None:
        skill_dir = get_built_in_skills_dir() / "live-director"
        self.assertTrue((skill_dir / "SKILL.md").is_file())
        self.assertTrue((skill_dir / "references" / "session.yaml").is_file())


class LiveDirectorStartSkillSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_pulse_workers_for_tests()
        self._original_personalities_dir = personality_module.get_personalities_dir()
        self._original_voices_dir = voices_module.get_voices_dir()
        from buddy_tools.infra.bootstrap import get_memory_root

        self._original_memory_root = get_memory_root()
        self.project_root = Path(__file__).resolve().parent.parent

        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.personalities_root = self.root / "personalities"
        self.voices_root = self.root / "voices"
        self.memory_root = self.root / "memory"
        self.personalities_root.mkdir()
        self.voices_root.mkdir()
        self.memory_root.mkdir()

        reset_data_dir_config(repo_root=self.project_root, data_dir=self.root / "data")
        set_personalities_dir(self.personalities_root)
        set_voices_dir(self.voices_root)
        set_memory_root(self.memory_root)

        voice_dir = self.voices_root / "cliff"
        voice_dir.mkdir(parents=True)
        (voice_dir / "audio.wav").write_bytes(b"RIFF")
        (voice_dir / "ref_text.txt").write_text("cliff transcript", encoding="utf-8")
        create_personality("coach", "Coach", "You are Coach.", voice_id="cliff")
        set_active_personality("coach")

    def tearDown(self) -> None:
        reset_pulse_workers_for_tests()
        reset_data_dir_config()
        set_personalities_dir(self._original_personalities_dir)
        set_voices_dir(self._original_voices_dir)
        if self._original_memory_root is not None:
            set_memory_root(self._original_memory_root)
        self._tmpdir.cleanup()

    @patch("buddy_tools.skills.start_pulse_worker")
    def test_start_live_director_initializes_pulse_state(self, mock_start_worker) -> None:
        result = execute_skill_tool(
            self.memory_root, "coach", "start_skill", {"name": "live-director"}
        )
        self.assertNotIn("Error", result.output)
        self.assertIn("pulse session", result.output.lower())

        skill_state = load_skill_state(self.memory_root, "coach")
        self.assertIsNotNone(skill_state)
        assert skill_state is not None
        self.assertEqual(skill_state.skill_name, "live-director")
        self.assertEqual(skill_state.skill_type, "pulse")

        mock_start_worker.assert_called_once()
        call_kwargs = mock_start_worker.call_args.kwargs
        self.assertEqual(call_kwargs.get("tick_interval_seconds"), 10.0)

        from buddy_tools.pulse.state import load_pulse_state

        pulse = load_pulse_state(self.memory_root, "coach")
        assert pulse is not None
        self.assertEqual(pulse.phase, "live")
        self.assertIn("last_camera_switch_at", pulse.vars)
        session = pulse.get_session_config()
        assert session is not None
        self.assertEqual(session.rules[0].id, "camera-switch")


if __name__ == "__main__":
    unittest.main()
