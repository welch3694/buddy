"""Tests for keyword barge-in via hey {persona name} (#182)."""

from __future__ import annotations

import unittest
from threading import Event
from unittest.mock import Mock, patch

from buddy_tools.voice.barge_in import (
    build_barge_in_instructions,
    build_wake_prefix,
    consume_barge_in_active,
    is_barge_in_active,
    match_barge_in_prefix,
    reset_barge_in_for_tests,
    set_barge_in_active,
)
from buddy_tools.voice.listening_pause import (
    ListeningPauseController,
    process_transcription_with_listening_pause,
)
from speech_to_speech.api.openai_realtime.runtime_config import RuntimeConfig
from speech_to_speech.pipeline.cancel_scope import CancelScope
from speech_to_speech.pipeline.messages import GenerateResponseRequest, Transcription


class WakePrefixMatchingTests(unittest.TestCase):
    def test_build_wake_prefix_normalizes_name(self) -> None:
        self.assertEqual(build_wake_prefix("Coach"), "hey coach")
        self.assertEqual(build_wake_prefix("Live Coach"), "hey live coach")

    def test_match_case_insensitive_and_punctuation(self) -> None:
        self.assertEqual(
            match_barge_in_prefix("Hey Coach, I need a water break", "Coach"),
            "I need a water break",
        )
        self.assertEqual(
            match_barge_in_prefix("hey coach: switch cameras", "Coach"),
            "switch cameras",
        )

    def test_match_multi_word_persona_name(self) -> None:
        self.assertEqual(
            match_barge_in_prefix("hey live coach, switch cameras", "Live Coach"),
            "switch cameras",
        )

    def test_match_persona_id_alias_when_display_name_differs(self) -> None:
        self.assertEqual(
            match_barge_in_prefix("hey buddy I need help", "Alex", persona_id="buddy"),
            "I need help",
        )
        self.assertIsNone(match_barge_in_prefix("hey buddy help", "Alex", persona_id="alex"))

    def test_empty_remainder_returns_empty_string(self) -> None:
        self.assertEqual(match_barge_in_prefix("hey coach", "Coach"), "")
        self.assertEqual(match_barge_in_prefix("Hey Coach!", "Coach"), "")

    def test_no_match_for_substring_or_wrong_name(self) -> None:
        self.assertIsNone(match_barge_in_prefix("say hey coach later", "Coach"))
        self.assertIsNone(match_barge_in_prefix("hey", "Coach"))
        self.assertIsNone(match_barge_in_prefix("hey buddy help me", "Coach"))
        self.assertIsNone(match_barge_in_prefix("please hey coach help", "Coach"))

    def test_build_barge_in_instructions_mentions_wake_phrase(self) -> None:
        text = build_barge_in_instructions("Coach")
        self.assertIn("hey Coach", text)
        self.assertIn("interrupt", text.lower())


class BargeInFlagTests(unittest.TestCase):
    def tearDown(self) -> None:
        reset_barge_in_for_tests()

    def test_set_and_consume_are_one_shot(self) -> None:
        reset_barge_in_for_tests()
        self.assertFalse(is_barge_in_active())
        set_barge_in_active(True)
        self.assertTrue(is_barge_in_active())
        self.assertTrue(consume_barge_in_active())
        self.assertFalse(is_barge_in_active())
        self.assertFalse(consume_barge_in_active())


class BargeInPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_barge_in_for_tests()
        self.controller = ListeningPauseController(
            cancel_scope=CancelScope(),
            should_listen=Event(),
        )
        self.notifier = Mock()
        self.notifier.text_output_queue = None
        self.notifier.should_listen = Event()
        self.notifier.runtime_config = RuntimeConfig()
        self.notifier.runtime_config.chat.add_item = Mock()

    def tearDown(self) -> None:
        reset_barge_in_for_tests()

    def _complete(self, text: str) -> list[GenerateResponseRequest]:
        transcription = Transcription(text=text, language_code=None, turn_id="t1", turn_revision=0)
        result = process_transcription_with_listening_pause(
            self.notifier,
            transcription,
            controller=self.controller,
        )
        return list(result)

    def test_strips_wake_and_commits_remainder(self) -> None:
        with patch(
            "buddy_tools.voice.barge_in.match_active_barge_in",
            return_value="I need a water break",
        ):
            with patch("buddy_tools.voice.barge_in.interrupt_for_barge_in") as interrupt:
                outputs = self._complete("hey coach, I need a water break")

        self.assertEqual(len(outputs), 1)
        self.assertIsInstance(outputs[0], GenerateResponseRequest)
        self.notifier.runtime_config.chat.add_item.assert_called_once()
        added = self.notifier.runtime_config.chat.add_item.call_args[0][0]
        self.assertIn("I need a water break", str(added))
        self.assertNotIn("hey coach", str(added).lower())
        interrupt.assert_called_once()
        # Flag consumed by commit path
        self.assertFalse(is_barge_in_active())

    def test_wake_while_paused_resumes_and_forwards_remainder(self) -> None:
        self.controller.pause()
        with patch(
            "buddy_tools.voice.barge_in.match_active_barge_in",
            return_value="tell me a joke please",
        ):
            with patch("buddy_tools.voice.barge_in.interrupt_for_barge_in") as interrupt:
                outputs = self._complete("hey buddy, tell me a joke please")

        self.assertFalse(self.controller.paused)
        self.assertEqual(len(outputs), 1)
        interrupt.assert_called_once()

    def test_empty_remainder_interrupts_without_commit(self) -> None:
        with patch("buddy_tools.voice.barge_in.match_active_barge_in", return_value=""):
            with patch("buddy_tools.voice.barge_in.interrupt_for_barge_in") as interrupt:
                outputs = self._complete("hey coach")

        self.assertEqual(outputs, [])
        self.notifier.runtime_config.chat.add_item.assert_not_called()
        interrupt.assert_called_once()
        self.assertTrue(is_barge_in_active())


if __name__ == "__main__":
    unittest.main()
