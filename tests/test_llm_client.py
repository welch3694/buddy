"""Tests for centralized LLM configuration."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from buddy_tools.infra.env import load_env_file, reset_env_file_state
from buddy_tools.infra.llm_client import (
    get_llm_model_name,
    require_llm_model_name,
    resolve_llm_gguf_path,
    resolve_llm_server_config,
)


class LlmClientConfigTests(unittest.TestCase):
    def tearDown(self) -> None:
        reset_env_file_state()
        for key in (
            "BUDDY_LLM_MODEL_NAME",
            "BUDDY_LLM_MODEL_DIR",
            "BUDDY_LLM_BASE_URL",
            "BUDDY_LLM_MMPROJ",
            "BUDDY_LLM_SERVER_EXE",
        ):
            os.environ.pop(key, None)

    def test_require_llm_model_name_raises_when_unset(self) -> None:
        with self.assertRaises(RuntimeError) as ctx:
            require_llm_model_name()
        self.assertIn("BUDDY_LLM_MODEL_NAME", str(ctx.exception))

    def test_resolve_from_dotenv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text(
                "BUDDY_LLM_MODEL_NAME=my-model-Q4_K_M\n"
                "BUDDY_LLM_MODEL_DIR=C:\\Models\n"
                "BUDDY_LLM_BASE_URL=http://127.0.0.1:9000/v1\n",
                encoding="utf-8",
            )
            with mock.patch("buddy_tools.infra.env._REPO_ROOT", Path(tmp)):
                reset_env_file_state()
                load_env_file()

            self.assertEqual(get_llm_model_name(), "my-model-Q4_K_M")
            self.assertEqual(resolve_llm_gguf_path(), Path(r"C:\Models\my-model-Q4_K_M.gguf"))
            config = resolve_llm_server_config()
            self.assertEqual(config["model_name"], "my-model-Q4_K_M")
            self.assertEqual(config["base_url"], "http://127.0.0.1:9000/v1")

    def test_cli_config_prints_json(self) -> None:
        os.environ["BUDDY_LLM_MODEL_NAME"] = "cli-model"
        from buddy_tools.infra.llm_client import _cli_main

        with mock.patch("sys.stdout") as stdout:
            stdout.write = lambda text: stdout.buffer.append(text)  # type: ignore[attr-defined]
            captured: list[str] = []

            def _capture(text: str) -> None:
                captured.append(text)

            with mock.patch("builtins.print", side_effect=_capture):
                code = _cli_main(["config"])
            self.assertEqual(code, 0)
            payload = json.loads("".join(captured))
            self.assertEqual(payload["model_name"], "cli-model")


if __name__ == "__main__":
    unittest.main()
