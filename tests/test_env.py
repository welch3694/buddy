"""Tests for .env loading."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from buddy_tools.env import load_env_file, reset_env_file_state


class LoadEnvFileTests(unittest.TestCase):
    def tearDown(self) -> None:
        reset_env_file_state()

    def test_loads_values_without_overriding_existing_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text(
                "TELEGRAM_BOT_TOKEN=from-dotenv\nBUDDY_DATA_DIR=C:\\data\\buddy\n",
                encoding="utf-8",
            )
            os.environ["TELEGRAM_BOT_TOKEN"] = "from-shell"

            with mock.patch("buddy_tools.env._REPO_ROOT", Path(tmp)):
                reset_env_file_state()
                loaded = load_env_file()

            self.assertEqual(loaded, env_path)
            self.assertEqual(os.environ.get("TELEGRAM_BOT_TOKEN"), "from-shell")
            self.assertEqual(os.environ.get("BUDDY_DATA_DIR"), r"C:\data\buddy")

            os.environ.pop("BUDDY_DATA_DIR", None)
            os.environ.pop("TELEGRAM_BOT_TOKEN", None)

    def test_returns_none_when_file_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("buddy_tools.env._REPO_ROOT", Path(tmp)):
                reset_env_file_state()
                self.assertIsNone(load_env_file())


if __name__ == "__main__":
    unittest.main()
