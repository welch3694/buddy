"""Tests for buddy_tools.infra.context_budget — working-context management."""

from __future__ import annotations

import unittest
from collections.abc import Iterator
from typing import Any
from unittest import mock
from unittest.mock import patch

from openai.types.realtime import RealtimeConversationItemFunctionCall
from openai.types.realtime.conversation_item import RealtimeConversationItemFunctionCallOutput
from openai.types.realtime.realtime_conversation_item_user_message import Content as UserContent

from buddy_tools.infra.context_budget import (
    ContextBudget,
    build_overflow_apology_text,
    estimate_chat_tokens,
    estimate_tokens,
    is_context_overflow_error,
    mask_old_tool_outputs,
    preflight_trim,
    recover_after_overflow,
)
from buddy_tools.core.patch import _iter_llm_outputs_with_context_budget
from speech_to_speech.api.openai_realtime.runtime_config import RuntimeConfig
from speech_to_speech.LLM.chat import Chat, make_user_message
from speech_to_speech.pipeline.messages import EndOfResponse, LLMResponseChunk


def _add_user(chat: Chat, text: str) -> None:
    chat.add_item(make_user_message(text))


def _add_tool_pair(chat: Chat, call_id: str, output: str) -> None:
    chat.add_item(
        RealtimeConversationItemFunctionCall(
            type="function_call",
            name="list_memory",
            arguments="{}",
            call_id=call_id,
        )
    )
    chat.add_item(
        RealtimeConversationItemFunctionCallOutput(
            type="function_call_output",
            call_id=call_id,
            output=output,
            status="completed",
        )
    )


class EstimateTokensTests(unittest.TestCase):
    def test_estimate_tokens_scales_with_length(self) -> None:
        self.assertGreater(estimate_tokens("x" * 400), estimate_tokens("short"))

    def test_estimate_chat_tokens_includes_system_tools_and_buffer(self) -> None:
        chat = Chat(10)
        instructions = "system prompt " * 50
        tools = [{"type": "function", "name": "remember", "description": "save memory"}]
        empty = estimate_chat_tokens(chat, instructions, tools)
        _add_user(chat, "hello " * 100)
        with_buffer = estimate_chat_tokens(chat, instructions, tools)
        self.assertGreater(with_buffer, empty)


class MaskOldToolOutputsTests(unittest.TestCase):
    def test_masks_old_outputs_keeps_recent(self) -> None:
        chat = Chat(20)
        budget = ContextBudget(mask_keep_recent_turns=1)
        _add_user(chat, "turn one")
        _add_tool_pair(chat, "call_1", "old verbose output " * 20)
        _add_user(chat, "turn two")
        _add_tool_pair(chat, "call_2", "recent verbose output " * 20)

        masked = mask_old_tool_outputs(chat, keep_recent_turns=1, budget=budget)
        self.assertEqual(masked, 1)
        outputs = [
            item.output
            for item in chat.buffer
            if isinstance(item, RealtimeConversationItemFunctionCallOutput)
        ]
        self.assertEqual(len(outputs), 2)
        self.assertIn("[tool result hidden", outputs[0])
        self.assertNotIn("[tool result hidden", outputs[1])

    def test_masked_chat_still_serializes(self) -> None:
        chat = Chat(20)
        _add_user(chat, "turn one")
        _add_tool_pair(chat, "call_1", "data " * 50)
        _add_user(chat, "turn two")
        mask_old_tool_outputs(chat, keep_recent_turns=0)
        chat.to_transformers_chat()
        chat.to_responses_api_chat()


class PreflightTrimTests(unittest.TestCase):
    def test_preflight_masks_then_evicts_under_tiny_budget(self) -> None:
        chat = Chat(20)
        instructions = "x" * 200
        tools: list[Any] = []
        budget = ContextBudget(ctx_size=120, output_reserve=10, safety_margin=10, mask_keep_recent_turns=0)

        for i in range(4):
            _add_user(chat, f"user turn {i} " + ("word " * 30))
            _add_tool_pair(chat, f"call_{i}", "tool output " * 40)

        report = preflight_trim(chat, instructions, tools, budget)
        self.assertTrue(report.acted)
        self.assertGreater(report.masked_outputs, 0)
        self.assertLessEqual(report.estimated_after, budget.effective_budget)
        self.assertGreaterEqual(chat._user_turn_count, 1)

    def test_preflight_noop_when_under_budget(self) -> None:
        chat = Chat(20)
        budget = ContextBudget(ctx_size=16384)
        _add_user(chat, "hi")
        report = preflight_trim(chat, "instructions", [], budget)
        self.assertFalse(report.acted)
        self.assertEqual(report.estimated_before, report.estimated_after)


class OverflowDetectionTests(unittest.TestCase):
    def test_matches_context_overflow_messages(self) -> None:
        self.assertTrue(is_context_overflow_error("request exceeds n_ctx limit"))
        self.assertTrue(is_context_overflow_error("context_length_exceeded"))
        self.assertTrue(is_context_overflow_error("KV cache capacity exceeded"))

    def test_rejects_unrelated_errors(self) -> None:
        self.assertFalse(is_context_overflow_error("connection refused"))
        self.assertFalse(is_context_overflow_error(None))


class RecoveryTests(unittest.TestCase):
    def test_recover_reduces_token_estimate(self) -> None:
        chat = Chat(20)
        instructions = "x" * 300
        budget = ContextBudget(ctx_size=200, output_reserve=20, safety_margin=20, mask_keep_recent_turns=0)
        for i in range(5):
            _add_user(chat, f"turn {i} " + ("long " * 40))
            _add_tool_pair(chat, f"call_{i}", "output " * 60)

        report = recover_after_overflow(chat, instructions, [], budget)
        self.assertTrue(report.acted)
        self.assertLess(report.estimated_after, report.estimated_before)


class PatchWrapperTests(unittest.TestCase):
    def test_overflow_end_of_response_yields_apology_then_clean_end(self) -> None:
        def fake_original(_handler: Any, _request: Any) -> Iterator[Any]:
            yield EndOfResponse(
                turn_id="turn_1",
                turn_revision=0,
                error="request exceeds n_ctx limit",
            )

        handler = mock.MagicMock()
        handler._turn_output_allowed.return_value = True

        runtime_config = RuntimeConfig()
        runtime_config.chat = Chat(5)
        runtime_config.session.instructions = "sys"
        runtime_config.session.tools = []
        _add_user(runtime_config.chat, "hello")

        request = mock.MagicMock()
        request.runtime_config = runtime_config
        request.response = None
        request.language_code = "en"
        request.speech_stopped_at_s = 0.0

        outputs = list(_iter_llm_outputs_with_context_budget(fake_original, handler, request))

        self.assertEqual(len(outputs), 2)
        self.assertIsInstance(outputs[0], LLMResponseChunk)
        self.assertEqual(outputs[0].text, build_overflow_apology_text())
        self.assertIsInstance(outputs[1], EndOfResponse)
        self.assertIsNone(outputs[1].error)
        runtime_config.chat.to_transformers_chat()

    def test_offered_tools_logged_before_generation(self) -> None:
        from buddy_tools.core import patch as patch_module

        def fake_original(_handler: Any, _request: Any) -> Iterator[Any]:
            yield EndOfResponse(turn_id="turn_1", turn_revision=0)

        handler = mock.MagicMock()
        handler._turn_output_allowed.return_value = True

        runtime_config = RuntimeConfig()
        runtime_config.chat = Chat(5)
        runtime_config.session.instructions = "sys"
        runtime_config.session.tools = [{"type": "function", "name": "start_skill"}]
        runtime_config.session.tool_choice = "auto"

        request = mock.MagicMock()
        request.runtime_config = runtime_config
        request.response = None
        request.language_code = "en"
        request.speech_stopped_at_s = 0.0
        request.turn_id = "turn_diag"
        request.turn_revision = 1

        with patch.object(patch_module.logger, "info") as info_mock:
            list(_iter_llm_outputs_with_context_budget(fake_original, handler, request))

        offered = [
            call
            for call in info_mock.call_args_list
            if call.args and isinstance(call.args[0], str) and call.args[0].startswith("Offered tools:")
        ]
        self.assertEqual(len(offered), 1)
        self.assertEqual(offered[0].args[1], 1)
        self.assertEqual(offered[0].args[2], "auto")
        self.assertEqual(offered[0].args[3], "turn_diag")
        self.assertEqual(offered[0].args[4], 1)


class GracefulDegradationTests(unittest.TestCase):
    def test_preflight_swallows_internal_errors(self) -> None:
        chat = Chat(5)
        with patch(
            "buddy_tools.infra.context_budget.estimate_chat_tokens",
            side_effect=RuntimeError("boom"),
        ):
            report = preflight_trim(chat, "x", [], ContextBudget())
        self.assertFalse(report.acted)


if __name__ == "__main__":
    unittest.main()
