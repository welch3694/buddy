"""Tests for tool failure logging helpers and dispatch."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from buddy_tools.core.executor import LocalToolExecutor, _log_tool_result
from buddy_tools.personality.tools import execute_personality_tool
from buddy_tools.core.registry import execute_tool
from buddy_tools.core.result import ToolExecutionResult
from buddy_tools.core.tool_logging import (
    is_tool_error,
    is_tool_error_output,
    log_tool_failure,
    safe_tool_context,
    tool_error,
)
from speech_to_speech.api.openai_realtime.runtime_config import RuntimeConfig
from speech_to_speech.LLM.chat import Chat


class ToolLoggingHelperTests(unittest.TestCase):
    def test_is_tool_error_detects_error_prefix(self) -> None:
        self.assertTrue(is_tool_error_output("Error: something went wrong"))
        self.assertFalse(is_tool_error_output("Success"))
        self.assertTrue(is_tool_error(ToolExecutionResult(output="Error: fail")))

    def test_safe_tool_context_truncates_and_redacts(self) -> None:
        context = safe_tool_context(
            {
                "personality_id": "coach",
                "description": "x" * 200,
                "content": "secret memory",
            }
        )
        self.assertEqual(context["personality_id"], "coach")
        self.assertTrue(str(context["description"]).endswith("..."))
        self.assertEqual(context["content"], "<13 chars>")

    def test_tool_error_logs_warning_and_returns_result(self) -> None:
        with self.assertLogs("buddy_tools.core.tool_logging", level="WARNING") as captured:
            result = tool_error("switch_personality", "personality_id is empty")
        self.assertEqual(result.output, "Error: personality_id is empty")
        self.assertIn("switch_personality", captured.output[0])
        self.assertIn("personality_id is empty", captured.output[0])

    def test_log_tool_failure_with_exc_uses_exception_level(self) -> None:
        exc = RuntimeError("boom")
        with self.assertLogs("buddy_tools.core.tool_logging", level="ERROR") as captured:
            log_tool_failure("capture_camera", "camera capture failed: boom", exc=exc)
        self.assertIn("capture_camera", captured.output[0])
        self.assertIn("boom", captured.output[0])


class RegistryToolLoggingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.memory_root = Path(self._tmpdir.name)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_execute_tool_invalid_json_logs_failure(self) -> None:
        with self.assertLogs("buddy_tools.core.tool_logging", level="WARNING") as captured:
            result = execute_tool(
                self.memory_root,
                "read_memory",
                "not-json",
                persona_namespace="buddy",
            )
        self.assertTrue(result.output.startswith("Error:"))
        self.assertIn("read_memory", captured.output[0])
        self.assertIn("invalid tool arguments JSON", captured.output[0])

    def test_execute_tool_unknown_tool_logs_failure(self) -> None:
        with self.assertLogs("buddy_tools.core.tool_logging", level="WARNING") as captured:
            result = execute_tool(
                self.memory_root,
                "nonexistent_tool",
                "{}",
                persona_namespace="buddy",
            )
        self.assertIn("unknown tool", result.output)
        self.assertIn("nonexistent_tool", captured.output[0])


class PersonalityToolLoggingTests(unittest.TestCase):
    def test_empty_personality_id_logs_tool_name(self) -> None:
        with self.assertLogs("buddy_tools.core.tool_logging", level="WARNING") as captured:
            result = execute_personality_tool("switch_personality", {"personality_id": ""})
        self.assertEqual(result.output, "Error: personality_id is empty")
        self.assertIn("switch_personality", captured.output[0])


class ExecutorToolLoggingTests(unittest.TestCase):
    def test_log_tool_result_failure_not_truncated(self) -> None:
        long_message = "Error: " + ("x" * 300)
        result = ToolExecutionResult(output=long_message)
        with self.assertLogs("buddy_tools.core.executor", level="ERROR") as captured:
            _log_tool_result("test_tool", result)
        self.assertIn(long_message, captured.output[0])
        self.assertNotIn(long_message[:120] + "...", captured.output[0])

    def test_log_tool_result_success_truncated(self) -> None:
        long_message = "ok " + ("y" * 300)
        result = ToolExecutionResult(output=long_message)
        with self.assertLogs("buddy_tools.core.executor", level="INFO") as captured:
            _log_tool_result("test_tool", result)
        self.assertNotIn(long_message, captured.output[0])
        self.assertIn("ok ", captured.output[0])

    def test_max_tool_rounds_logs_pending_tool_names(self) -> None:
        chat = Chat(2)
        runtime_config = RuntimeConfig()
        runtime_config.chat = chat
        pending_context = MagicMock()
        pending_context.runtime_config = runtime_config
        follow_up_queue = MagicMock()

        executor = LocalToolExecutor(MagicMock(), MagicMock(), MagicMock())
        executor.setup(text_prompt_queue=follow_up_queue)
        executor._tool_rounds = 5
        executor._pending_context = pending_context

        tool_a = MagicMock()
        tool_a.name = "capture_camera"
        tool_a.call_id = "call_a"
        tool_b = MagicMock()
        tool_b.name = "append_memory"
        tool_b.call_id = "call_b"
        executor._pending_tools = [tool_a, tool_b]

        with self.assertLogs("buddy_tools.core.executor", level="WARNING") as captured:
            ran = executor._execute_pending_tools()
        self.assertTrue(ran)
        follow_up_queue.put.assert_called_once_with(pending_context)
        log_line = captured.output[0]
        self.assertIn("capture_camera", log_line)
        self.assertIn("append_memory", log_line)
        self.assertIn("skipping 2 tools", log_line)


if __name__ == "__main__":
    unittest.main()
