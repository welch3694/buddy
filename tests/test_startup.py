"""Tests for buddy_tools.startup."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from buddy_tools import bootstrap as bootstrap_module
from buddy_tools import personality as personality_module
from buddy_tools import voices as voices_module
from buddy_tools.data_dir import configure_user_data, reset_data_dir_config
from buddy_tools.personality import create_personality, set_active_personality
from buddy_tools.startup import (
    FIXED_VOICE_INSTRUCTIONS,
    build_init_instructions,
    build_voice_system_prompt,
    resolve_startup_config,
)
from buddy_tools.voices import set_voices_dir


class StartupConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_personalities_dir = personality_module.get_personalities_dir()
        self._original_voices_dir = voices_module.get_voices_dir()
        self._original_memory_root = bootstrap_module.get_memory_root()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.repo_root = self.root / "repo"
        self.data_dir = self.root / "data"
        (self.repo_root / "personalities").mkdir(parents=True)
        self.voices_root = self.root / "voices"
        self.voices_root.mkdir()
        set_voices_dir(self.voices_root)
        self._write_voice("cliff")
        self._write_voice("narrator")
        reset_data_dir_config(repo_root=self.repo_root, data_dir=self.data_dir)
        configure_user_data()

    def tearDown(self) -> None:
        reset_data_dir_config()
        personality_module.set_personalities_dir(self._original_personalities_dir)
        set_voices_dir(self._original_voices_dir)
        bootstrap_module.set_memory_root(self._original_memory_root)
        self._tmpdir.cleanup()

    def _write_voice(self, voice_id: str, ref_text: str = "Reference transcript.") -> None:
        voice_dir = self.voices_root / voice_id
        voice_dir.mkdir(parents=True, exist_ok=True)
        (voice_dir / "audio.wav").write_bytes(b"RIFF")
        (voice_dir / "ref_text.txt").write_text(ref_text, encoding="utf-8")

    def test_build_voice_system_prompt_appends_fixed_rules(self) -> None:
        result = build_voice_system_prompt("You are Buddy.")

        self.assertIn("You are Buddy.", result)
        self.assertIn(FIXED_VOICE_INSTRUCTIONS.splitlines()[0], result)

    def test_build_init_instructions_uses_active_personality(self) -> None:
        create_personality("buddy", "Buddy", "You are Buddy.", voice_id="cliff")
        create_personality("coach", "Coach", "You are Coach.", voice_id="narrator")
        set_active_personality("coach")

        instructions = build_init_instructions()

        self.assertIn("You are Coach.", instructions)
        self.assertIn("natural spoken language", instructions)

    def test_resolve_startup_config_matches_active_personality_voice(self) -> None:
        create_personality("buddy", "Buddy", "Buddy prompt.", voice_id="cliff")
        create_personality("coach", "Coach", "Coach prompt.", voice_id="narrator")
        set_active_personality("coach")

        config = resolve_startup_config()

        self.assertEqual(config["personality_id"], "coach")
        self.assertEqual(config["voice_id"], "narrator")
        self.assertIn("Coach prompt.", config["init_chat_prompt"])
        self.assertTrue(
            str(config["audio"]).endswith("narrator\\audio.wav")
            or str(config["audio"]).endswith("narrator/audio.wav")
        )
        self.assertEqual(config["ref_text"], "Reference transcript.")
        self.assertEqual(config["data_dir"], str(self.data_dir.resolve()))

    def test_resolve_startup_config_is_json_serializable(self) -> None:
        create_personality("buddy", "Buddy", "Buddy prompt.", voice_id="cliff")
        payload = json.dumps(resolve_startup_config())
        data = json.loads(payload)
        self.assertEqual(data["personality_id"], "buddy")


class ProjectStartupTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self._original_personalities_dir = personality_module.get_personalities_dir()
        self._original_memory_root = bootstrap_module.get_memory_root()
        self.repo_root = Path(__file__).resolve().parent.parent
        self.data_dir = self.root / "userdata"
        reset_data_dir_config(repo_root=self.repo_root, data_dir=self.data_dir)

    def tearDown(self) -> None:
        reset_data_dir_config()
        personality_module.set_personalities_dir(self._original_personalities_dir)
        bootstrap_module.set_memory_root(self._original_memory_root)
        self._tmpdir.cleanup()

    def test_repo_startup_config_uses_buddy_and_cliff(self) -> None:
        config = resolve_startup_config()
        self.assertEqual(config["personality_id"], "buddy")
        self.assertEqual(config["voice_id"], "cliff")
        self.assertIn("Buddy", config["personality_name"])
        self.assertTrue(Path(config["audio"]).is_file())
        self.assertEqual(config["data_dir"], str(self.data_dir.resolve()))


if __name__ == "__main__":
    unittest.main()
