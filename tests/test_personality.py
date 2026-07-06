"""Tests for buddy_tools.personality."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from buddy_tools import personality as personality_module
import buddy_tools.voice.voices as voices_module
from buddy_tools.personality import (
    DEFAULT_PERSONALITY_ID,
    PersonalityProfile,
    create_personality,
    delete_personality,
    get_active_personality,
    get_active_personality_id,
    get_personality,
    list_personalities,
    set_active_personality,
    set_personalities_dir,
    update_personality,
)
from buddy_tools.voice.voices import set_voices_dir


class PersonalityManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_personalities_dir = personality_module.get_personalities_dir()
        self._original_voices_dir = voices_module.get_voices_dir()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.personalities_root = self.root / "personalities"
        self.voices_root = self.root / "voices"
        self.personalities_root.mkdir()
        self.voices_root.mkdir()
        set_personalities_dir(self.personalities_root)
        set_voices_dir(self.voices_root)
        self._write_voice("cliff")

    def tearDown(self) -> None:
        set_personalities_dir(self._original_personalities_dir)
        set_voices_dir(self._original_voices_dir)
        self._tmpdir.cleanup()

    def _write_voice(self, voice_id: str) -> None:
        voice_dir = self.voices_root / voice_id
        voice_dir.mkdir(parents=True, exist_ok=True)
        (voice_dir / "audio.wav").write_bytes(b"RIFF")
        (voice_dir / "ref_text.txt").write_text("Test voice reference.", encoding="utf-8")

    def _write_personality(
        self,
        personality_id: str,
        *,
        name: str = "Test",
        voice_id: str = "cliff",
        prompt: str = "You are a test assistant.",
    ) -> Path:
        directory = self.personalities_root / personality_id
        directory.mkdir(parents=True, exist_ok=True)
        profile = {
            "id": personality_id,
            "name": name,
            "description": "Test personality",
            "voice_id": voice_id,
            "behaviors": {"verbosity": "concise"},
            "memory_namespace": personality_id,
        }
        import yaml

        (directory / "profile.yaml").write_text(yaml.safe_dump(profile, sort_keys=False), encoding="utf-8")
        (directory / "prompt.md").write_text(prompt + "\n", encoding="utf-8")
        return directory

    def test_list_personalities_discovers_complete_folders(self) -> None:
        self._write_personality("buddy")
        self._write_personality("coach")
        incomplete = self.personalities_root / "broken"
        incomplete.mkdir()
        (incomplete / "profile.yaml").write_text("id: broken\n", encoding="utf-8")

        self.assertEqual(list_personalities(), ["buddy", "coach"])

    def test_get_personality_loads_profile_and_prompt(self) -> None:
        self._write_personality("buddy", name="Buddy", prompt="You are Buddy.")
        profile = get_personality("buddy")

        self.assertIsInstance(profile, PersonalityProfile)
        self.assertEqual(profile.id, "buddy")
        self.assertEqual(profile.name, "Buddy")
        self.assertEqual(profile.voice_id, "cliff")
        self.assertEqual(profile.prompt, "You are Buddy.")
        self.assertEqual(profile.behaviors["verbosity"], "concise")

    def test_active_personality_defaults_to_buddy(self) -> None:
        self.assertEqual(get_active_personality_id(), DEFAULT_PERSONALITY_ID)

    def test_set_and_get_active_personality(self) -> None:
        self._write_personality("buddy")
        self._write_personality("coach", name="Coach")

        set_active_personality("coach")
        self.assertEqual(get_active_personality_id(), "coach")
        self.assertEqual(get_active_personality().name, "Coach")

    def test_get_active_personality_falls_back_to_buddy(self) -> None:
        self._write_personality("buddy", name="Buddy")
        (self.personalities_root / "active.json").write_text(json.dumps({"id": "missing"}), encoding="utf-8")

        profile = get_active_personality()
        self.assertEqual(profile.id, "buddy")

    def test_create_personality(self) -> None:
        profile = create_personality(
            "coach",
            "Coach",
            "You are a motivating coach.",
            description="Fitness coach",
            voice_id="cliff",
            behaviors={"warmth": "low"},
        )

        self.assertEqual(profile.id, "coach")
        self.assertEqual(profile.name, "Coach")
        self.assertTrue((self.personalities_root / "coach" / "profile.yaml").is_file())
        self.assertIn("motivating coach", profile.prompt)

    def test_update_personality(self) -> None:
        self._write_personality("coach", name="Coach", prompt="Old prompt.")
        updated = update_personality("coach", name="Head Coach", prompt="New prompt.")

        self.assertEqual(updated.name, "Head Coach")
        self.assertEqual(updated.prompt, "New prompt.")

    def test_delete_personality(self) -> None:
        self._write_personality("buddy")
        self._write_personality("coach", name="Coach")

        delete_personality("coach")
        self.assertEqual(list_personalities(), ["buddy"])

    def test_delete_buddy_allowed(self) -> None:
        self._write_personality("buddy")
        delete_personality("buddy")
        self.assertEqual(list_personalities(), [])

    def test_delete_active_personality_resets_active_to_buddy(self) -> None:
        self._write_personality("buddy")
        self._write_personality("coach", name="Coach")
        set_active_personality("coach")

        delete_personality("coach")

        self.assertEqual(get_active_personality_id(), "buddy")

    def test_invalid_personality_id_raises(self) -> None:
        with self.assertRaises(ValueError):
            get_personality("")


class ProjectPersonalitiesTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self._original_personalities_dir = personality_module.get_personalities_dir()
        self._original_memory_root = None
        import buddy_tools.infra.bootstrap as bootstrap_module

        self._original_memory_root = bootstrap_module.get_memory_root()
        from buddy_tools.infra.data_dir import configure_user_data, reset_data_dir_config

        self.repo_root = Path(__file__).resolve().parent.parent
        self.data_dir = self.root / "userdata"
        reset_data_dir_config(repo_root=self.repo_root, data_dir=self.data_dir)
        configure_user_data()

    def tearDown(self) -> None:
        from buddy_tools.infra.data_dir import reset_data_dir_config
        import buddy_tools.infra.bootstrap as bootstrap_module

        reset_data_dir_config()
        set_personalities_dir(self._original_personalities_dir)
        if self._original_memory_root is not None:
            bootstrap_module.set_memory_root(self._original_memory_root)
        self._tmpdir.cleanup()

    def test_repo_buddy_personality_seeds_into_data_dir(self) -> None:
        profile = get_active_personality(validate_voice=False)

        self.assertEqual(profile.id, "buddy")
        self.assertIn("Buddy", profile.name)
        self.assertEqual(profile.voice_id, "cliff")
        self.assertTrue((self.data_dir / "personalities" / "buddy" / "profile.yaml").is_file())
        self.assertTrue((self.data_dir / "voices" / "cliff" / "audio.wav").is_file())


if __name__ == "__main__":
    unittest.main()
