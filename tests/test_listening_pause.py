"""Tests for stop/start listening voice commands (#22)."""

from __future__ import annotations

import unittest
from threading import Event
from unittest.mock import Mock

from buddy_tools.voice.listening_pause import (
    ListeningPauseController,
    build_listening_pause_instructions,
    get_listening_pause_controller,
    matches_start_listening,
    matches_stop_listening,
    normalize_transcript,
    process_transcription_with_listening_pause,
)
from speech_to_speech.api.openai_realtime.runtime_config import RuntimeConfig
from speech_to_speech.pipeline.cancel_scope import CancelScope
from speech_to_speech.pipeline.messages import GenerateResponseRequest, PartialTranscription, Transcription


class PhraseMatchingTests(unittest.TestCase):
    def test_build_listening_pause_instructions_mentions_exact_phrases(self) -> None:
        text = build_listening_pause_instructions()
        self.assertIn("stop listening", text)
        self.assertIn("start listening", text)
        self.assertIn("exact", text.lower())

    def test_normalize_transcript_strips_punctuation(self) -> None:
        self.assertEqual(normalize_transcript("Stop listening!"), "stop listening")

    def test_matches_stop_listening_exact_phrase_only(self) -> None:
        self.assertTrue(matches_stop_listening("stop listening"))
        self.assertTrue(matches_stop_listening("Stop listening!"))

    def test_matches_start_listening_exact_phrase_only(self) -> None:
        self.assertTrue(matches_start_listening("start listening"))
        self.assertTrue(matches_start_listening("Start listening."))

    def test_does_not_match_embedded_or_extended_phrases(self) -> None:
        self.assertFalse(matches_stop_listening("hey buddy stop listening"))
        self.assertFalse(matches_stop_listening("I'd like you to stop listening now."))
        self.assertFalse(matches_stop_listening("test out your stop listening command"))
        self.assertFalse(matches_stop_listening("please stop talking"))
        self.assertFalse(matches_start_listening("please start listening"))
        self.assertFalse(matches_start_listening("Can you start listening again?"))
        self.assertFalse(matches_start_listening("start the music"))


class ListeningPauseControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = ListeningPauseController(
            cancel_scope=CancelScope(),
            should_listen=Event(),
        )

    def test_pause_cancels_in_flight_response(self) -> None:
        generation = self.controller.cancel_scope.generation
        self.controller.pause()
        self.assertTrue(self.controller.paused)
        self.assertTrue(self.controller.cancel_scope.is_stale(generation))
        self.assertTrue(self.controller.should_listen.is_set())

    def test_resume_only_when_paused(self) -> None:
        self.assertFalse(self.controller.resume())
        self.controller.pause()
        self.assertTrue(self.controller.resume())
        self.assertFalse(self.controller.paused)


class ProcessTranscriptionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = ListeningPauseController(should_listen=Event())
        self.notifier = Mock()
        self.notifier.text_output_queue = None
        self.notifier.should_listen = Event()
        self.notifier.runtime_config = RuntimeConfig()
        self.notifier.runtime_config.chat.add_item = Mock()

    def _complete(self, text: str) -> list[GenerateResponseRequest]:
        transcription = Transcription(text=text, language_code=None, turn_id="t1", turn_revision=0)
        result = process_transcription_with_listening_pause(
            self.notifier,
            transcription,
            controller=self.controller,
        )
        return list(result)

    def test_forwards_normal_speech_when_active(self) -> None:
        outputs = self._complete("What is the weather?")
        self.assertEqual(len(outputs), 1)
        self.assertIsInstance(outputs[0], GenerateResponseRequest)
        self.notifier.runtime_config.chat.add_item.assert_called_once()

    def test_stop_listening_pauses_without_llm_request(self) -> None:
        outputs = self._complete("stop listening")
        self.assertEqual(outputs, [])
        self.assertTrue(self.controller.paused)
        self.notifier.runtime_config.chat.add_item.assert_not_called()

    def test_stop_listening_embedded_in_sentence_is_not_a_command(self) -> None:
        outputs = self._complete("I'd like you to stop listening now.")
        self.assertEqual(len(outputs), 1)
        self.assertFalse(self.controller.paused)
        self.notifier.runtime_config.chat.add_item.assert_called_once()

    def test_ignored_speech_while_paused(self) -> None:
        self.controller.pause()
        outputs = self._complete("tell me a joke")
        self.assertEqual(outputs, [])
        self.notifier.runtime_config.chat.add_item.assert_not_called()

    def test_start_listening_resumes_without_llm_request(self) -> None:
        self.controller.pause()
        self.notifier.should_listen.clear()
        outputs = self._complete("start listening")
        self.assertEqual(outputs, [])
        self.assertFalse(self.controller.paused)
        self.notifier.runtime_config.chat.add_item.assert_not_called()
        self.assertTrue(self.notifier.should_listen.is_set())

    def test_start_listening_when_active_reenables_listen(self) -> None:
        self.notifier.should_listen.clear()
        outputs = self._complete("start listening")
        self.assertEqual(outputs, [])
        self.notifier.runtime_config.chat.add_item.assert_not_called()
        self.assertTrue(self.notifier.should_listen.is_set())

    def test_partial_transcription_while_paused_is_not_forwarded(self) -> None:
        self.controller.pause()
        partial = PartialTranscription(text="hello", turn_id="t1", turn_revision=0)
        result = process_transcription_with_listening_pause(
            self.notifier,
            partial,
            controller=self.controller,
        )
        self.assertEqual(list(result), [])


class TranscriptionNotifierPatchTests(unittest.TestCase):
    def setUp(self) -> None:
        controller = get_listening_pause_controller()
        controller.paused = False
        controller.cancel_scope = None
        controller.should_listen = None

    def test_patch_routes_through_listening_pause_gate(self) -> None:
        # Patch only the notifier — avoid apply_patches() TTS/pipeline imports (~7s).
        from buddy_tools.core.patch import _patch_transcription_notifier_listening_pause
        from speech_to_speech.STT.transcription_notifier import TranscriptionNotifier

        _patch_transcription_notifier_listening_pause()

        notifier = TranscriptionNotifier(Mock(), queue_in=Mock(), queue_out=Mock())
        notifier.text_output_queue = None
        notifier.should_listen = Event()
        notifier.runtime_config = RuntimeConfig()
        notifier.runtime_config.chat.add_item = Mock()

        list(
            notifier.process(
                Transcription(text="stop listening", language_code=None, turn_id="t1", turn_revision=0)
            )
        )

        self.assertTrue(get_listening_pause_controller().paused)
        notifier.runtime_config.chat.add_item.assert_not_called()

    def test_configure_listening_pause_from_handlers(self) -> None:
        from buddy_tools.core.patch import _configure_listening_pause_from_handlers

        cancel_scope = CancelScope()
        should_listen = Event()
        lm_handler = Mock()
        lm_handler.cancel_scope = cancel_scope

        _configure_listening_pause_from_handlers([lm_handler], should_listen=should_listen)

        controller = get_listening_pause_controller()
        self.assertIs(controller.should_listen, should_listen)
        self.assertIs(controller.cancel_scope, cancel_scope)


if __name__ == "__main__":
    unittest.main()
