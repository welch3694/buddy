"""Tests for voice turn-state observability (#84)."""

from __future__ import annotations

import logging
import unittest
from queue import Queue
from threading import Event, Timer
from unittest.mock import Mock, patch

from buddy_tools.voice.endpointing import (
    configure_endpointing,
    process_with_endpointing_gate,
    reset_endpointing_for_tests,
)
from buddy_tools.voice.listening_pause import (
    ListeningPauseController,
    process_transcription_with_listening_pause,
)
from buddy_tools.voice.turn_completion_heuristic import reset_heuristic_config_for_tests
from buddy_tools.voice.turn_state import (
    HOLDING_STATUS_MESSAGE,
    VoiceTurnState,
    configure_turn_state,
    current_turn_state,
    reset_turn_state_for_tests,
    set_turn_state,
)
from speech_to_speech.api.openai_realtime.runtime_config import RuntimeConfig
from speech_to_speech.pipeline.events import PartialTranscriptionEvent
from speech_to_speech.pipeline.messages import PartialTranscription


class TurnStateControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_turn_state_for_tests()

    def tearDown(self) -> None:
        reset_turn_state_for_tests()

    def test_transition_logs_once_per_state(self) -> None:
        with self.assertLogs("buddy_tools.voice.turn_state", level=logging.INFO) as logged:
            self.assertTrue(set_turn_state(VoiceTurnState.HOLDING, reason="test"))
            self.assertFalse(set_turn_state(VoiceTurnState.HOLDING, reason="test"))
            self.assertTrue(set_turn_state(VoiceTurnState.GENERATING, reason="test"))
        messages = [record.getMessage() for record in logged.records]
        self.assertEqual(sum("Turn state: holding" in m for m in messages), 1)
        self.assertEqual(sum("Turn state: generating" in m for m in messages), 1)

    def test_announce_ui_emits_holding_status(self) -> None:
        queue: Queue = Queue()
        configure_turn_state(text_output_queue=queue)
        set_turn_state(VoiceTurnState.HOLDING, announce_ui=True, turn_id="t1", turn_revision=0)
        event = queue.get_nowait()
        self.assertIsInstance(event, PartialTranscriptionEvent)
        self.assertEqual(event.delta, HOLDING_STATUS_MESSAGE)
        self.assertEqual(event.turn_id, "t1")


class TurnStateIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_turn_state_for_tests()
        reset_endpointing_for_tests()
        reset_heuristic_config_for_tests()
        self.text_prompt_queue: Queue = Queue()
        self.text_output_queue: Queue = Queue()
        self.runtime_config = RuntimeConfig()
        self.runtime_config.chat.add_item = Mock()
        self.tracker = Mock()
        configure_endpointing(
            text_prompt_queue=self.text_prompt_queue,
            runtime_config=self.runtime_config,
            speculative_turns=self.tracker,
            should_listen=Event(),
        )
        configure_turn_state(text_output_queue=self.text_output_queue)
        self.notifier = Mock()
        self.notifier.text_output_queue = self.text_output_queue
        self.notifier.should_listen = Event()
        self.notifier.runtime_config = self.runtime_config

    def tearDown(self) -> None:
        reset_endpointing_for_tests()
        reset_heuristic_config_for_tests()
        reset_turn_state_for_tests()

    def test_endpointing_hold_sets_holding_state(self) -> None:
        self.tracker.try_is_latest_after_reopen_grace.return_value = None
        self.tracker.has_pending_reopen_or_grace.return_value = True
        self.tracker.is_committed.return_value = False

        with patch.object(Timer, "start", lambda self: None):
            result = process_with_endpointing_gate(
                self.notifier,
                transcript="I was thinking",
                language_code=None,
                turn_id="t-hold",
                turn_revision=0,
                speech_stopped_at_s=1.0,
            )
            self.assertEqual(list(result), [])

        self.assertEqual(current_turn_state(), VoiceTurnState.HOLDING)
        status_events = [
            item
            for item in list(self.text_output_queue.queue)
            if isinstance(item, PartialTranscriptionEvent) and item.delta == HOLDING_STATUS_MESSAGE
        ]
        self.assertEqual(len(status_events), 1)

    def test_pause_and_resume_update_turn_state(self) -> None:
        controller = ListeningPauseController(should_listen=Event())
        with self.assertLogs("buddy_tools.voice.turn_state", level=logging.INFO) as logged:
            controller.pause()
            self.assertEqual(current_turn_state(), VoiceTurnState.PAUSED)
            controller.resume()
            self.assertEqual(current_turn_state(), VoiceTurnState.LISTENING)
        messages = " ".join(record.getMessage() for record in logged.records)
        self.assertIn("Turn state: paused", messages)
        self.assertIn("Turn state: listening", messages)

    def test_paused_partial_keeps_paused_prefix(self) -> None:
        controller = ListeningPauseController(should_listen=Event())
        controller.pause()
        while not self.text_output_queue.empty():
            self.text_output_queue.get_nowait()
        result = process_transcription_with_listening_pause(
            self.notifier,
            PartialTranscription(text="hello", turn_id="t1", turn_revision=0),
            controller=controller,
        )
        self.assertEqual(list(result), [])
        event = self.text_output_queue.get_nowait()
        self.assertIsInstance(event, PartialTranscriptionEvent)
        self.assertTrue(event.delta.startswith("[paused - ignored]"))

    def test_commit_sets_generating(self) -> None:
        self.tracker.try_is_latest_after_reopen_grace.return_value = True
        self.tracker.is_committed.return_value = False
        self.tracker.has_pending_reopen_or_grace.return_value = False
        self.tracker.commit = Mock()

        result = process_with_endpointing_gate(
            self.notifier,
            transcript="What time is it?",
            language_code=None,
            turn_id="t-gen",
            turn_revision=0,
            speech_stopped_at_s=1.0,
        )
        outputs = list(result)
        self.assertEqual(len(outputs), 1)
        self.assertEqual(current_turn_state(), VoiceTurnState.GENERATING)


if __name__ == "__main__":
    unittest.main()
