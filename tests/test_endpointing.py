"""Tests for endpointing gate (#79)."""

from __future__ import annotations

import unittest
from queue import Queue
from threading import Event, Timer
from unittest.mock import Mock, patch

from buddy_tools.core.patch import _ensure_speculative_turns
from buddy_tools.voice.endpointing import (
    configure_endpointing,
    get_endpointing_gate,
    merge_transcripts,
    process_with_endpointing_gate,
    reset_endpointing_for_tests,
)
from buddy_tools.voice.listening_pause import (
    ListeningPauseController,
    process_transcription_with_listening_pause,
)
from speech_to_speech.api.openai_realtime.runtime_config import RuntimeConfig
from speech_to_speech.pipeline.messages import GenerateResponseRequest, Transcription


class EnsureSpeculativeTurnsTests(unittest.TestCase):
    def test_creates_tracker_when_local_pipeline_omits_it(self) -> None:
        from speech_to_speech.arguments_classes.vad_arguments import VADHandlerArguments
        from speech_to_speech.pipeline.speculative_turns import SpeculativeTurnTracker

        vad_kwargs = VADHandlerArguments()
        kwargs: dict = {"vad_handler_kwargs": vad_kwargs}
        tracker = _ensure_speculative_turns(kwargs)
        self.assertIsInstance(tracker, SpeculativeTurnTracker)
        self.assertIs(kwargs["speculative_turns"], tracker)
        self.assertIs(vars(vad_kwargs)["speculative_turns"], tracker)

    def test_reuses_existing_tracker(self) -> None:
        from speech_to_speech.pipeline.speculative_turns import SpeculativeTurnTracker

        existing = SpeculativeTurnTracker()
        kwargs = {"speculative_turns": existing}
        self.assertIs(_ensure_speculative_turns(kwargs), existing)


class MergeTranscriptsTests(unittest.TestCase):
    def test_prefers_longer_superset(self) -> None:
        self.assertEqual(
            merge_transcripts("hello", "hello world"),
            "hello world",
        )

    def test_appends_continuation(self) -> None:
        self.assertEqual(
            merge_transcripts("I need", "more time"),
            "I need more time",
        )


class EndpointingGateTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_endpointing_for_tests()
        self.queue: Queue = Queue()
        self.runtime_config = RuntimeConfig()
        self.runtime_config.chat.add_item = Mock()
        self.notifier = Mock()
        self.notifier.text_output_queue = None
        self.notifier.should_listen = Event()
        self.notifier.runtime_config = self.runtime_config
        self.tracker = Mock()
        configure_endpointing(
            text_prompt_queue=self.queue,
            runtime_config=self.runtime_config,
            speculative_turns=self.tracker,
        )

    def tearDown(self) -> None:
        reset_endpointing_for_tests()

    def _observe(self, text: str, *, turn_id: str = "t1", revision: int = 0) -> list[GenerateResponseRequest]:
        result = process_with_endpointing_gate(
            self.notifier,
            transcript=text,
            language_code=None,
            turn_id=turn_id,
            turn_revision=revision,
            speech_stopped_at_s=100.0,
        )
        if result is None:
            return []
        return list(result)

    def test_hold_does_not_commit(self) -> None:
        self.tracker.try_is_latest_after_reopen_grace.return_value = None
        self.tracker.has_pending_reopen_or_grace.return_value = True
        self.tracker.is_committed.return_value = False

        outputs = self._observe("hello there")

        self.assertEqual(outputs, [])
        self.runtime_config.chat.add_item.assert_not_called()
        self.assertTrue(get_endpointing_gate()._pending is not None)

    def test_hold_resume_merge_single_commit(self) -> None:
        self.tracker.is_committed.return_value = False
        self.tracker.try_is_latest_after_reopen_grace.side_effect = [None, True]
        self.tracker.has_pending_reopen_or_grace.return_value = True

        with patch.object(Timer, "start", lambda self: None):
            first = self._observe("I was thinking")
            self.assertEqual(first, [])
            self.runtime_config.chat.add_item.assert_not_called()

            second = self._observe("I was thinking about lunch", revision=1)
        self.assertEqual(len(second), 1)
        self.assertIsInstance(second[0], GenerateResponseRequest)
        self.runtime_config.chat.add_item.assert_called_once()
        args = self.runtime_config.chat.add_item.call_args[0][0]
        self.assertEqual(args.content[0].text, "I was thinking about lunch")

    def test_hold_release_commit_via_timer(self) -> None:
        self.tracker.is_committed.return_value = False
        self.tracker.try_is_latest_after_reopen_grace.side_effect = [None, True]
        self.tracker.has_pending_reopen_or_grace.return_value = False
        self.tracker.commit = Mock()

        with patch.object(Timer, "start", lambda self: None):
            outputs = self._observe("finish this thought")
            self.assertEqual(outputs, [])

        get_endpointing_gate()._on_release_timer()

        self.assertFalse(self.queue.empty())
        request = self.queue.get_nowait()
        self.assertIsInstance(request, GenerateResponseRequest)
        self.runtime_config.chat.add_item.assert_called_once()
        self.tracker.commit.assert_called_once_with("t1", 0)

    def test_no_tracker_passthrough(self) -> None:
        reset_endpointing_for_tests()
        controller = ListeningPauseController(should_listen=Event())
        outputs = list(
            process_transcription_with_listening_pause(
                self.notifier,
                Transcription(text="immediate commit", language_code=None, turn_id="t1", turn_revision=0),
                controller=controller,
            )
        )
        self.assertEqual(len(outputs), 1)
        self.assertIsInstance(outputs[0], GenerateResponseRequest)
        self.runtime_config.chat.add_item.assert_called_once()

    def test_listening_pause_still_blocks_before_endpointing(self) -> None:
        controller = ListeningPauseController(should_listen=Event())
        controller.pause()
        self.tracker.try_commit_if_latest_after_reopen_grace.return_value = None
        configure_endpointing(
            text_prompt_queue=self.queue,
            runtime_config=self.runtime_config,
            speculative_turns=self.tracker,
        )

        outputs = list(
            process_transcription_with_listening_pause(
                self.notifier,
                Transcription(text="ignored speech", language_code=None, turn_id="t1", turn_revision=0),
                controller=controller,
            )
        )

        self.assertEqual(outputs, [])
        self.runtime_config.chat.add_item.assert_not_called()
        self.assertIsNone(get_endpointing_gate()._pending)


if __name__ == "__main__":
    unittest.main()
