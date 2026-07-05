"""Tests for buddy_tools.core.executor — tool output chat recording."""

from __future__ import annotations

import unittest
from queue import Queue
from threading import Event
from unittest.mock import patch

from openai.types.realtime import RealtimeConversationItemFunctionCall
from openai.types.responses.response_function_tool_call import ResponseFunctionToolCall

from buddy_tools.channels.telegram import text_only_response_params
from buddy_tools.core.executor import LocalToolExecutor
from buddy_tools.core.result import ToolExecutionResult
from speech_to_speech.api.openai_realtime.runtime_config import RuntimeConfig
from speech_to_speech.LLM.chat import Chat
from speech_to_speech.pipeline.messages import GenerateResponseRequest


class ToolOutputChatRecordingTests(unittest.TestCase):
    def test_tool_output_recorded_with_id_for_compaction(self) -> None:
        """function_call_output must have an id; append_tool_output skips _ensure_id."""
        chat = Chat(2)
        chat.add_item(
            RealtimeConversationItemFunctionCall(
                type="function_call",
                name="list_skills",
                arguments="{}",
                call_id="call_test123",
            )
        )

        runtime_config = RuntimeConfig()
        runtime_config.chat = chat
        pending_context = GenerateResponseRequest(
            runtime_config=runtime_config,
            response=text_only_response_params(),
            language_code="en",
            turn_id="turn_1",
            turn_revision=0,
            speech_stopped_at_s=0.0,
        )

        executor = LocalToolExecutor(Event(), Queue(), Queue())
        executor.setup(text_prompt_queue=Queue())
        executor._pending_context = pending_context
        executor._pending_tools = [
            ResponseFunctionToolCall(
                type="function_call",
                name="list_skills",
                arguments="{}",
                call_id="call_test123",
                id="fc_test",
            )
        ]

        with patch(
            "buddy_tools.core.executor.execute_tool",
            return_value=ToolExecutionResult(output='[{"name":"demo"}]'),
        ):
            self.assertTrue(executor._execute_pending_tools())

        outputs = [
            item
            for item in chat.buffer
            if getattr(item, "type", None) == "function_call_output"
        ]
        self.assertEqual(len(outputs), 1)
        self.assertIsNotNone(outputs[0].id)
        self.assertTrue(str(outputs[0].id).startswith("fco_"))

        # Compaction snapshot must not hit item.id assertion.
        snapshot = chat.to_responses_api_chat(items=chat.buffer)
        self.assertTrue(any(entry.get("type") == "function_call_output" for entry in snapshot))


if __name__ == "__main__":
    unittest.main()
