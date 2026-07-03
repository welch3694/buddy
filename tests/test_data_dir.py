"""Tests for buddy_tools.data_dir."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from buddy_tools import bootstrap as bootstrap_module
from buddy_tools import personality as personality_module
from buddy_tools.data_dir import (
    configure_user_data,
    default_data_dir,
    migrate_legacy_user_data,
    reset_data_dir_config,
    resolve_data_dir,
    seed_shipped_personalities,
)
from buddy_tools.personality import (
    delete_personality,
    get_active_personality,
    get_personalities_dir,
    list_personalities,
    update_personality,
)


class DataDirPathTests(unittest.TestCase):
    def tearDown(self) -> None:
        reset_data_dir_config()
        os.environ.pop("BUDDY_DATA_DIR", None)
        os.environ.pop("XDG_DATA_HOME", None)

    def test_buddy_data_dir_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["BUDDY_DATA_DIR"] = tmp
            self.assertEqual(resolve_data_dir(), Path(tmp).resolve())

    @mock.patch.dict(os.environ, {"LOCALAPPDATA": r"C:\Users\test\AppData\Local"}, clear=False)
    @mock.patch("sys.platform", "win32")
    def test_default_data_dir_windows(self) -> None:
        self.assertEqual(default_data_dir(), Path(r"C:\Users\test\AppData\Local\Buddy"))

    @mock.patch("sys.platform", "darwin")
    def test_default_data_dir_macos(self) -> None:
        expected = Path.home() / "Library" / "Application Support" / "Buddy"
        self.assertEqual(default_data_dir(), expected)

    @mock.patch.dict(os.environ, {"XDG_DATA_HOME": "/custom/xdg"}, clear=False)
    @mock.patch("sys.platform", "linux")
    def test_default_data_dir_linux_xdg(self) -> None:
        self.assertEqual(default_data_dir(), Path("/custom/xdg/buddy"))

    @mock.patch("buddy_tools.data_dir.Path.home", return_value=Path("/home/testuser"))
    @mock.patch.dict(os.environ, {}, clear=True)
    @mock.patch("sys.platform", "linux")
    def test_default_data_dir_linux_fallback(self, _mock_home: mock.MagicMock) -> None:
        expected = Path("/home/testuser/.local/share/buddy")
        self.assertEqual(default_data_dir(), expected)


class SeedAndMigrateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.repo_root = self.root / "repo"
        self.data_dir = self.root / "data"
        self.shipped = self.repo_root / "personalities"
        self.user_personalities = self.data_dir / "personalities"
        self._original_personalities_dir = personality_module.get_personalities_dir()
        self._original_memory_root = bootstrap_module.get_memory_root()

    def tearDown(self) -> None:
        reset_data_dir_config()
        personality_module.set_personalities_dir(self._original_personalities_dir)
        bootstrap_module.set_memory_root(self._original_memory_root)
        self._tmpdir.cleanup()

    def _write_shipped_personality(self, personality_id: str, *, name: str | None = None) -> None:
        directory = self.shipped / personality_id
        directory.mkdir(parents=True, exist_ok=True)
        profile = (
            f"id: {personality_id}\n"
            f"name: {name or personality_id.title()}\n"
            f"voice_id: cliff\n"
            f"memory_namespace: {personality_id}\n"
        )
        (directory / "profile.yaml").write_text(profile, encoding="utf-8")
        (directory / "prompt.md").write_text(f"You are {personality_id}.\n", encoding="utf-8")

    def test_seed_copies_missing_personalities(self) -> None:
        self._write_shipped_personality("buddy")
        self._write_shipped_personality("coach")

        seeded = seed_shipped_personalities(self.shipped, self.user_personalities)

        self.assertEqual(seeded, ["buddy", "coach"])
        self.assertTrue((self.user_personalities / "buddy" / "profile.yaml").is_file())
        self.assertTrue((self.user_personalities / "coach" / "prompt.md").is_file())

    def test_seed_skips_existing_complete_personality(self) -> None:
        self._write_shipped_personality("buddy")
        self.user_personalities.mkdir(parents=True)
        existing = self.user_personalities / "buddy"
        shutil.copytree(self.shipped / "buddy", existing)
        (existing / "prompt.md").write_text("Custom buddy prompt.\n", encoding="utf-8")

        seeded = seed_shipped_personalities(self.shipped, self.user_personalities)

        self.assertEqual(seeded, [])
        self.assertIn("Custom buddy prompt", (existing / "prompt.md").read_text(encoding="utf-8"))

    def test_seed_replaces_incomplete_personality(self) -> None:
        self._write_shipped_personality("buddy")
        broken = self.user_personalities / "buddy"
        broken.mkdir(parents=True)
        (broken / "profile.yaml").write_text("id: buddy\n", encoding="utf-8")

        seeded = seed_shipped_personalities(self.shipped, self.user_personalities)

        self.assertEqual(seeded, ["buddy"])
        self.assertTrue((broken / "prompt.md").is_file())

    def test_migrate_legacy_memory_and_active_json(self) -> None:
        repo_memory = self.repo_root / "memory" / "global"
        repo_memory.mkdir(parents=True)
        (repo_memory / "notes.md").write_text("# Notes\n- color: blue\n", encoding="utf-8")
        legacy_active = self.repo_root / "personalities"
        legacy_active.mkdir(parents=True)
        (legacy_active / "active.json").write_text(json.dumps({"id": "buddy"}), encoding="utf-8")

        actions = migrate_legacy_user_data(self.repo_root, self.data_dir)

        self.assertTrue(any("memory" in action for action in actions))
        self.assertTrue(any("active.json" in action for action in actions))
        self.assertIn("color: blue", (self.data_dir / "memory" / "global" / "notes.md").read_text(encoding="utf-8"))
        self.assertTrue((self.user_personalities / "active.json").is_file())

    def test_configure_user_data_wires_runtime_paths(self) -> None:
        self._write_shipped_personality("buddy")
        reset_data_dir_config(repo_root=self.repo_root, data_dir=self.data_dir)

        returned = configure_user_data()

        self.assertEqual(returned, self.data_dir.resolve())
        self.assertEqual(bootstrap_module.get_memory_root(), self.data_dir / "memory")
        self.assertEqual(get_personalities_dir(), self.data_dir / "personalities")
        self.assertEqual(list_personalities(), ["buddy"])

    def test_delete_buddy_and_reconfigure_reseeds(self) -> None:
        self._write_shipped_personality("buddy", name="Buddy")
        reset_data_dir_config(repo_root=self.repo_root, data_dir=self.data_dir)
        configure_user_data()

        delete_personality("buddy")
        self.assertEqual(list_personalities(), [])

        configure_user_data()
        self.assertEqual(list_personalities(), ["buddy"])
        profile = get_active_personality(validate_voice=False)
        self.assertEqual(profile.name, "Buddy")

    def test_update_buddy_writes_to_data_dir(self) -> None:
        self._write_shipped_personality("buddy", name="Buddy")
        reset_data_dir_config(repo_root=self.repo_root, data_dir=self.data_dir)
        configure_user_data()

        updated = update_personality("buddy", prompt="Edited buddy prompt.", validate_voice=False)

        self.assertEqual(updated.prompt, "Edited buddy prompt.")
        prompt_path = self.data_dir / "personalities" / "buddy" / "prompt.md"
        self.assertIn("Edited buddy prompt", prompt_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
