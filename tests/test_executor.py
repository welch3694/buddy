"""Tests for buddy_tools.core.executor — tool output chat recording."""

from __future__ import annotations

import unittest
from queue import Queue
from threading import Event
from unittest.mock import patch

from openai.types.realtime import RealtimeConversationItemFunctionCall
from openai.types.responses.response_function_tool_call import ResponseFunctionToolCall

from buddy_tools.channels.telegram import text_only_response_params
from buddy_tools.core.executor import CLAIM_TTS_FALLBACK, LocalToolExecutor, MAX_TOOL_ROUNDS
from buddy_tools.core.result import ToolExecutionResult
from speech_to_speech.api.openai_realtime.runtime_config import RuntimeConfig
from speech_to_speech.LLM.chat import Chat
from speech_to_speech.pipeline.messages import EndOfResponse, GenerateResponseRequest, LLMResponseChunk


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

        self.assertEqual(len(executor._turn_receipts), 1)
        self.assertEqual(executor._turn_receipts[0].tool, "list_skills")
        self.assertEqual(executor._turn_receipts[0].status, "ok")


class MaxToolRoundFallbackTests(unittest.TestCase):
    def test_skipped_tools_inject_errors_and_queue_follow_up(self) -> None:
        chat = Chat(2)
        chat.add_item(
            RealtimeConversationItemFunctionCall(
                type="function_call",
                name="read_episodic_summary",
                arguments='{"level":"day","date":"yesterday"}',
                call_id="call_skipped",
            )
        )

        runtime_config = RuntimeConfig()
        runtime_config.chat = chat
        follow_up_queue: Queue[GenerateResponseRequest] = Queue()
        pending_context = GenerateResponseRequest(
            runtime_config=runtime_config,
            response=text_only_response_params(),
            language_code="en",
            turn_id="turn_1",
            turn_revision=0,
            speech_stopped_at_s=0.0,
        )

        executor = LocalToolExecutor(Event(), Queue(), Queue())
        executor.setup(text_prompt_queue=follow_up_queue)
        executor._pending_context = pending_context
        executor._tool_rounds = MAX_TOOL_ROUNDS
        executor._pending_tools = [
            ResponseFunctionToolCall(
                type="function_call",
                name="read_episodic_summary",
                arguments='{"level":"day","date":"yesterday"}',
                call_id="call_skipped",
                id="fc_skipped",
            )
        ]

        self.assertTrue(executor._execute_pending_tools())
        self.assertEqual(executor._pending_tools, [])
        self.assertFalse(follow_up_queue.empty())

        outputs = [
            item
            for item in chat.buffer
            if getattr(item, "type", None) == "function_call_output"
        ]
        self.assertEqual(len(outputs), 1)
        self.assertIn("tool round limit reached", outputs[0].output)

        self.assertEqual(len(executor._turn_receipts), 1)
        self.assertEqual(executor._turn_receipts[0].status, "skipped")


class ClaimWithoutReceiptTtsGateTests(unittest.TestCase):
    def test_fabricated_claim_is_held_and_fallback_spoken(self) -> None:
        executor = LocalToolExecutor(Event(), Queue(), Queue())
        executor.setup(text_prompt_queue=Queue())
        runtime_config = RuntimeConfig()
        runtime_config.chat = Chat(2)

        claim = "I'm starting the live-director skill now so we can get everything synced up."
        chunk = LLMResponseChunk(
            text=claim,
            language_code="en",
            runtime_config=runtime_config,
            response=text_only_response_params(),
            turn_id="turn_bypass",
            turn_revision=0,
            speech_stopped_at_s=0.0,
        )
        end = EndOfResponse(turn_id="turn_bypass", turn_revision=0)

        with (
            patch("buddy_tools.core.executor.handle_pulse_response_chunk", side_effect=lambda c: c),
            patch("buddy_tools.core.executor.handle_pulse_end_of_response"),
            patch("buddy_tools.core.executor.record_assistant_speech_for_active_pulse") as record_speech,
            patch("buddy_tools.core.executor._log_episodic_assistant_turn") as log_episodic,
            patch("buddy_tools.companion.publisher.emit_assistant_text") as emit_text,
            self.assertLogs("buddy_tools.core.tool_logging", level="WARNING") as captured,
        ):
            chunk_outputs = list(executor.process(chunk))
            outputs = list(executor.process(end))

        self.assertEqual(chunk_outputs, [])
        self.assertEqual(len(outputs), 2)
        self.assertIsInstance(outputs[0], LLMResponseChunk)
        self.assertEqual(outputs[0].text, CLAIM_TTS_FALLBACK)
        self.assertIsInstance(outputs[1], EndOfResponse)
        self.assertTrue(any("tool_bypass" in line for line in captured.output))
        self.assertEqual(executor._turn_receipts, [])
        record_speech.assert_called_once_with(CLAIM_TTS_FALLBACK)
        log_episodic.assert_called_once_with("turn_bypass", CLAIM_TTS_FALLBACK)
        emit_text.assert_called_once()
        self.assertEqual(emit_text.call_args.args[0], CLAIM_TTS_FALLBACK)

    def test_non_claim_speech_still_flows(self) -> None:
        executor = LocalToolExecutor(Event(), Queue(), Queue())
        executor.setup(text_prompt_queue=Queue())
        runtime_config = RuntimeConfig()
        runtime_config.chat = Chat(2)

        text = "The weather looks nice today."
        chunk = LLMResponseChunk(
            text=text,
            language_code="en",
            runtime_config=runtime_config,
            response=text_only_response_params(),
            turn_id="turn_chat",
            turn_revision=0,
            speech_stopped_at_s=0.0,
        )
        end = EndOfResponse(turn_id="turn_chat", turn_revision=0)

        with (
            patch("buddy_tools.core.executor.handle_pulse_response_chunk", side_effect=lambda c: c),
            patch("buddy_tools.core.executor.handle_pulse_end_of_response"),
            patch("buddy_tools.core.executor.record_assistant_speech_for_active_pulse") as record_speech,
            patch("buddy_tools.core.executor._log_episodic_assistant_turn"),
            patch("buddy_tools.companion.publisher.emit_assistant_text") as emit_text,
        ):
            chunk_outputs = list(executor.process(chunk))
            with self.assertNoLogs("buddy_tools.core.tool_logging", level="WARNING"):
                outputs = list(executor.process(end))

        self.assertEqual(len(chunk_outputs), 1)
        self.assertIsInstance(chunk_outputs[0], LLMResponseChunk)
        self.assertEqual(chunk_outputs[0].text, text)
        self.assertEqual(len(outputs), 1)
        self.assertIsInstance(outputs[0], EndOfResponse)
        record_speech.assert_called_once_with(text)
        emit_text.assert_called_once()
        self.assertEqual(emit_text.call_args.args[0], text)

    def test_claim_with_receipt_does_not_warn(self) -> None:
        executor = LocalToolExecutor(Event(), Queue(), Queue())
        executor.setup(text_prompt_queue=Queue())
        runtime_config = RuntimeConfig()
        runtime_config.chat = Chat(2)

        chat = runtime_config.chat
        chat.add_item(
            RealtimeConversationItemFunctionCall(
                type="function_call",
                name="start_skill",
                arguments='{"name":"live-director"}',
                call_id="call_start",
            )
        )
        pending_context = GenerateResponseRequest(
            runtime_config=runtime_config,
            response=text_only_response_params(),
            language_code="en",
            turn_id="turn_ok",
            turn_revision=0,
            speech_stopped_at_s=0.0,
        )
        executor._pending_context = pending_context
        executor._pending_tools = [
            ResponseFunctionToolCall(
                type="function_call",
                name="start_skill",
                arguments='{"name":"live-director"}',
                call_id="call_start",
                id="fc_start",
            )
        ]

        with patch(
            "buddy_tools.core.executor.execute_tool",
            return_value=ToolExecutionResult(output="Skill started."),
        ):
            self.assertTrue(executor._execute_pending_tools())

        claim = "I'm starting the live-director skill now."
        chunk = LLMResponseChunk(
            text=claim,
            language_code="en",
            runtime_config=runtime_config,
            response=text_only_response_params(),
            turn_id="turn_ok",
            turn_revision=0,
            speech_stopped_at_s=0.0,
        )
        end = EndOfResponse(turn_id="turn_ok", turn_revision=0)

        with (
            patch("buddy_tools.core.executor.handle_pulse_response_chunk", side_effect=lambda c: c),
            patch("buddy_tools.core.executor.handle_pulse_end_of_response"),
            patch("buddy_tools.core.executor.record_assistant_speech_for_active_pulse"),
            patch("buddy_tools.core.executor._log_episodic_assistant_turn"),
            patch("buddy_tools.companion.publisher.emit_assistant_text") as emit_text,
        ):
            chunk_outputs = list(executor.process(chunk))
            with self.assertNoLogs("buddy_tools.core.tool_logging", level="WARNING"):
                outputs = list(executor.process(end))

        self.assertEqual(len(chunk_outputs), 1)
        self.assertEqual(chunk_outputs[0].text, claim)
        self.assertEqual(len(outputs), 1)
        self.assertIsInstance(outputs[0], EndOfResponse)
        emit_text.assert_called_once()
        self.assertEqual(emit_text.call_args.args[0], claim)


class SilentActionIntentFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        from buddy_tools.voice.action_intents import reset_action_intent_stash_for_tests

        reset_action_intent_stash_for_tests()

    def tearDown(self) -> None:
        from buddy_tools.voice.action_intents import reset_action_intent_stash_for_tests

        reset_action_intent_stash_for_tests()

    def test_empty_tools_silently_executes_stashed_intent(self) -> None:
        from buddy_tools.voice.action_intents import ActionIntent, stash_action_intent

        follow_up: Queue = Queue()
        executor = LocalToolExecutor(Event(), Queue(), Queue())
        executor.setup(text_prompt_queue=follow_up)
        runtime_config = RuntimeConfig()
        runtime_config.chat = Chat(2)
        pending_context = GenerateResponseRequest(
            runtime_config=runtime_config,
            response=text_only_response_params(),
            language_code="en",
            turn_id="turn_silent",
            turn_revision=0,
            speech_stopped_at_s=0.0,
        )
        executor._pending_context = pending_context
        stash_action_intent(
            "turn_silent",
            ActionIntent(tool_name="start_skill", arguments={"name": "remember"}),
        )

        claim = "I've remembered your preference — no more babe or baby."
        chunk = LLMResponseChunk(
            text=claim,
            language_code="en",
            runtime_config=runtime_config,
            response=text_only_response_params(),
            turn_id="turn_silent",
            turn_revision=0,
            speech_stopped_at_s=0.0,
        )
        end = EndOfResponse(turn_id="turn_silent", turn_revision=0)

        with (
            patch("buddy_tools.core.executor.handle_pulse_response_chunk", side_effect=lambda c: c),
            patch("buddy_tools.core.executor.handle_pulse_end_of_response"),
            patch("buddy_tools.core.executor.record_assistant_speech_for_active_pulse") as record_speech,
            patch("buddy_tools.core.executor._log_episodic_assistant_turn"),
            patch("buddy_tools.companion.publisher.emit_assistant_text") as emit_text,
            patch(
                "buddy_tools.core.executor.execute_tool",
                return_value=ToolExecutionResult(
                    output="Started remember.",
                    refresh_instructions=True,
                    include_full_skill_body=True,
                ),
            ) as execute_tool,
        ):
            chunk_outputs = list(executor.process(chunk))
            outputs = list(executor.process(end))

        self.assertEqual(chunk_outputs, [])  # claim held until EOR
        self.assertEqual(outputs, [])  # silent path swallows EOR; follow-up queued
        execute_tool.assert_called_once()
        self.assertEqual(execute_tool.call_args.args[1], "start_skill")
        self.assertEqual(execute_tool.call_args.args[2], '{"name": "remember"}')
        self.assertFalse(follow_up.empty())
        self.assertEqual(len(executor._turn_receipts), 1)
        self.assertEqual(executor._turn_receipts[0].tool, "start_skill")
        record_speech.assert_not_called()
        emit_text.assert_not_called()

        types = [getattr(item, "type", None) for item in runtime_config.chat.buffer]
        self.assertIn("function_call", types)
        self.assertIn("function_call_output", types)

    def test_llm_tools_clear_stash_without_double_execute(self) -> None:
        from buddy_tools.voice.action_intents import ActionIntent, pop_action_intent, stash_action_intent

        follow_up: Queue = Queue()
        executor = LocalToolExecutor(Event(), Queue(), Queue())
        executor.setup(text_prompt_queue=follow_up)
        runtime_config = RuntimeConfig()
        runtime_config.chat = Chat(2)
        runtime_config.chat.add_item(
            RealtimeConversationItemFunctionCall(
                type="function_call",
                name="start_skill",
                arguments='{"name":"remember"}',
                call_id="call_llm",
            )
        )
        pending_context = GenerateResponseRequest(
            runtime_config=runtime_config,
            response=text_only_response_params(),
            language_code="en",
            turn_id="turn_ok",
            turn_revision=0,
            speech_stopped_at_s=0.0,
        )
        executor._pending_context = pending_context
        stash_action_intent(
            "turn_ok",
            ActionIntent(tool_name="start_skill", arguments={"name": "remember"}),
        )

        tool_chunk = LLMResponseChunk(
            text="",
            language_code="en",
            runtime_config=runtime_config,
            response=text_only_response_params(),
            turn_id="turn_ok",
            turn_revision=0,
            speech_stopped_at_s=0.0,
            tools=[
                ResponseFunctionToolCall(
                    type="function_call",
                    name="start_skill",
                    arguments='{"name":"remember"}',
                    call_id="call_llm",
                    id="fc_llm",
                )
            ],
        )
        end = EndOfResponse(turn_id="turn_ok", turn_revision=0)

        with (
            patch("buddy_tools.core.executor.handle_pulse_response_chunk", side_effect=lambda c: c),
            patch(
                "buddy_tools.core.executor.execute_tool",
                return_value=ToolExecutionResult(output="Started remember."),
            ) as execute_tool,
        ):
            list(executor.process(tool_chunk))
            outputs = list(executor.process(end))

        self.assertEqual(outputs, [])
        execute_tool.assert_called_once()
        self.assertIsNone(pop_action_intent("turn_ok"))


if __name__ == "__main__":
    unittest.main()
