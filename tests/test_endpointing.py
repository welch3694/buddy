"""Tests for endpointing gate (#79, #80)."""

from __future__ import annotations

import unittest
from queue import Queue
from threading import Event, Timer
from unittest.mock import Mock, patch

from buddy_tools.core.patch import _ensure_speculative_turns
from buddy_tools.voice.turn_completion_heuristic import HeuristicConfig, reset_heuristic_config_for_tests
from buddy_tools.voice.endpointing import (
    PendingUtterance,
    build_commit_request,
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
from buddy_tools.voice.short_utterance_gate import reset_short_utterance_config_for_tests
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
        reset_heuristic_config_for_tests()
        reset_short_utterance_config_for_tests()
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
        reset_heuristic_config_for_tests()
        reset_short_utterance_config_for_tests()

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
        self.assertIsNone(outputs[0].response)

    def test_action_intent_forces_tool_choice_on_commit(self) -> None:
        from buddy_tools.voice.action_intents import pop_action_intent, reset_action_intent_stash_for_tests

        reset_action_intent_stash_for_tests()
        request = build_commit_request(
            self.runtime_config,
            PendingUtterance(
                transcript="start director",
                language_code=None,
                speech_stopped_at_s=1.0,
                turn_id="t1",
                turn_revision=0,
            ),
        )
        self.assertIsNotNone(request)
        assert request is not None
        self.assertIsNotNone(request.response)
        assert request.response is not None
        tool_choice = request.response.tool_choice
        self.assertIsNotNone(tool_choice)
        self.assertEqual(tool_choice.name, "skill")
        self.assertEqual(tool_choice.type, "function")
        stashed = pop_action_intent("t1")
        self.assertIsNotNone(stashed)
        assert stashed is not None
        self.assertEqual(stashed.arguments, {"action": "start", "name": "live-director"})
        reset_action_intent_stash_for_tests()

    def test_unmatched_commit_leaves_response_none(self) -> None:
        request = build_commit_request(
            self.runtime_config,
            PendingUtterance(
                transcript="hello there",
                language_code=None,
                speech_stopped_at_s=1.0,
                turn_id="t1",
                turn_revision=0,
            ),
        )
        self.assertIsNotNone(request)
        assert request is not None
        self.assertIsNone(request.response)

    def test_fold_pending_cue_attaches_instructions_on_commit(self) -> None:
        import tempfile
        from pathlib import Path

        import yaml
        from openai.types.realtime.realtime_response_create_params import RealtimeResponseCreateParams

        from buddy_tools.infra.bootstrap import set_memory_root
        from buddy_tools.infra.data_dir import reset_data_dir_config
        from buddy_tools.personality import create_personality, set_active_personality, set_personalities_dir
        from buddy_tools.pulse.inject import reset_pulse_inject_for_tests
        from buddy_tools.pulse.schema import parse_session_config
        from buddy_tools.pulse.state import build_pulse_state_from_session, save_pulse_state
        from buddy_tools.voice.voices import set_voices_dir

        reset_pulse_inject_for_tests()
        reset_data_dir_config()
        tmpdir = tempfile.TemporaryDirectory()
        try:
            root = Path(tmpdir.name)
            personalities_root = root / "personalities"
            voices_root = root / "voices"
            memory_root = root / "memory"
            for path in (personalities_root, voices_root, memory_root):
                path.mkdir(parents=True)

            set_personalities_dir(personalities_root)
            set_voices_dir(voices_root)
            set_memory_root(memory_root)

            voice_dir = voices_root / "cliff"
            voice_dir.mkdir(parents=True)
            (voice_dir / "audio.wav").write_bytes(b"RIFF")
            (voice_dir / "ref_text.txt").write_text("cliff", encoding="utf-8")
            create_personality("coach", "Coach", "You are Coach.", voice_id="cliff")
            set_active_personality("coach")

            session = parse_session_config(
                yaml.safe_load(
                    """
name: live-director
pulse:
  tick_interval_s: 5
  mandatory_cue_max_defer_s: 30
init:
  set:
    phase: live
rules: []
schedule: []
"""
                ),
                skill_name="live-director",
            )
            state = build_pulse_state_from_session("live-director", session)
            state.pending_cue = "Switch to camera 2."
            state.cue_priority = "mandatory"
            state.fold_on_next_reply = True
            save_pulse_state(memory_root, "coach", state)

            from speech_to_speech.LLM.chat import Chat

            self.runtime_config.chat = Chat(4)
            self.runtime_config.session.instructions = "Base system prompt."

            request = build_commit_request(
                self.runtime_config,
                PendingUtterance(
                    transcript="I was saying something important",
                    language_code=None,
                    speech_stopped_at_s=1.0,
                    turn_id="t1",
                    turn_revision=0,
                ),
            )
            self.assertIsNotNone(request)
            assert request is not None
            self.assertIsNotNone(request.response)
            assert isinstance(request.response, RealtimeResponseCreateParams)
            assert request.response.instructions is not None
            self.assertIn("Switch to camera 2.", request.response.instructions)
            self.assertIn("fold-into-reply", request.response.instructions.lower())
        finally:
            reset_pulse_inject_for_tests()
            tmpdir.cleanup()
            reset_data_dir_config()

    def test_action_intent_skips_fold_weave(self) -> None:
        import tempfile
        from pathlib import Path

        import yaml

        from buddy_tools.infra.bootstrap import set_memory_root
        from buddy_tools.infra.data_dir import reset_data_dir_config
        from buddy_tools.personality import create_personality, set_active_personality, set_personalities_dir
        from buddy_tools.pulse.inject import reset_pulse_inject_for_tests
        from buddy_tools.pulse.schema import parse_session_config
        from buddy_tools.pulse.state import build_pulse_state_from_session, load_pulse_state, save_pulse_state
        from buddy_tools.voice.action_intents import reset_action_intent_stash_for_tests
        from buddy_tools.voice.voices import set_voices_dir

        reset_pulse_inject_for_tests()
        reset_action_intent_stash_for_tests()
        reset_data_dir_config()
        tmpdir = tempfile.TemporaryDirectory()
        try:
            root = Path(tmpdir.name)
            personalities_root = root / "personalities"
            voices_root = root / "voices"
            memory_root = root / "memory"
            for path in (personalities_root, voices_root, memory_root):
                path.mkdir(parents=True)

            set_personalities_dir(personalities_root)
            set_voices_dir(voices_root)
            set_memory_root(memory_root)

            voice_dir = voices_root / "cliff"
            voice_dir.mkdir(parents=True)
            (voice_dir / "audio.wav").write_bytes(b"RIFF")
            (voice_dir / "ref_text.txt").write_text("cliff", encoding="utf-8")
            create_personality("coach", "Coach", "You are Coach.", voice_id="cliff")
            set_active_personality("coach")

            session = parse_session_config(
                yaml.safe_load(
                    """
name: live-director
pulse:
  tick_interval_s: 5
init:
  set:
    phase: live
rules: []
schedule: []
"""
                ),
                skill_name="live-director",
            )
            state = build_pulse_state_from_session("live-director", session)
            state.pending_cue = "Switch to camera 2."
            state.cue_priority = "mandatory"
            state.fold_on_next_reply = True
            save_pulse_state(memory_root, "coach", state)

            from speech_to_speech.LLM.chat import Chat

            self.runtime_config.chat = Chat(4)

            request = build_commit_request(
                self.runtime_config,
                PendingUtterance(
                    transcript="start director",
                    language_code=None,
                    speech_stopped_at_s=1.0,
                    turn_id="t1",
                    turn_revision=0,
                ),
            )
            self.assertIsNotNone(request)
            assert request is not None
            assert request.response is not None
            self.assertIsNotNone(request.response.tool_choice)
            # Fold must not have started — cue still pending with fold flag.
            loaded = load_pulse_state(memory_root, "coach")
            assert loaded is not None
            self.assertEqual(loaded.pending_cue, "Switch to camera 2.")
            self.assertTrue(loaded.fold_on_next_reply)
            self.assertFalse(loaded.pulse_in_flight)
        finally:
            reset_action_intent_stash_for_tests()
            reset_pulse_inject_for_tests()
            tmpdir.cleanup()
            reset_data_dir_config()

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

    def test_heuristic_continue_extends_hold(self) -> None:
        self.tracker.is_committed.return_value = False
        self.tracker.try_is_latest_after_reopen_grace.return_value = True
        self.tracker.start_reopen_grace = Mock()

        with patch.object(Timer, "start", lambda self: None):
            outputs = self._observe("I was thinking um")

        self.assertEqual(outputs, [])
        self.runtime_config.chat.add_item.assert_not_called()
        self.tracker.start_reopen_grace.assert_called_once()
        self.assertIsNotNone(get_endpointing_gate()._pending)

    def test_heuristic_unknown_commits_when_ready(self) -> None:
        self.tracker.is_committed.return_value = False
        self.tracker.try_is_latest_after_reopen_grace.return_value = True
        self.tracker.commit = Mock()

        outputs = self._observe("Hello there")

        self.assertEqual(len(outputs), 1)
        self.assertIsInstance(outputs[0], GenerateResponseRequest)
        self.runtime_config.chat.add_item.assert_called_once()
        self.tracker.start_reopen_grace.assert_not_called()

    def test_heuristic_disabled_commits_immediately(self) -> None:
        from buddy_tools.voice import turn_completion_heuristic

        self.tracker.is_committed.return_value = False
        self.tracker.try_is_latest_after_reopen_grace.return_value = True
        self.tracker.commit = Mock()
        disabled = HeuristicConfig(enabled=False)

        with patch.object(turn_completion_heuristic, "get_heuristic_config", return_value=disabled):
            outputs = self._observe("I was thinking um")

        self.assertEqual(len(outputs), 1)
        self.runtime_config.chat.add_item.assert_called_once()
        self.tracker.start_reopen_grace.assert_not_called()


    def test_continue_hold_caps_at_two_then_commits(self) -> None:
        self.tracker.is_committed.return_value = False
        self.tracker.try_is_latest_after_reopen_grace.return_value = True
        self.tracker.start_reopen_grace = Mock()
        self.tracker.commit = Mock()

        with patch.object(Timer, "start", lambda self: None):
            outputs = self._observe("I was thinking um")

        self.assertEqual(outputs, [])
        self.assertEqual(self.tracker.start_reopen_grace.call_count, 1)

        gate = get_endpointing_gate()
        gate._on_release_timer()
        self.assertEqual(self.tracker.start_reopen_grace.call_count, 2)
        self.runtime_config.chat.add_item.assert_not_called()

        gate._on_release_timer()
        self.runtime_config.chat.add_item.assert_called_once()

    def test_continue_hold_resets_when_user_resumes(self) -> None:
        self.tracker.is_committed.return_value = False
        self.tracker.try_is_latest_after_reopen_grace.return_value = True
        self.tracker.start_reopen_grace = Mock()
        self.tracker.commit = Mock()

        with patch.object(Timer, "start", lambda self: None):
            self._observe("I was thinking um", revision=0)
            gate = get_endpointing_gate()
            gate._on_release_timer()
            self.assertEqual(gate._continue_hold_count, 2)
            self.assertEqual(self.tracker.start_reopen_grace.call_count, 2)

            second = self._observe("I was thinking um and like", revision=1)
            self.assertEqual(second, [])
            # Reset on merge, then one CONTINUE for the new trailing "like".
            self.assertEqual(gate._continue_hold_count, 1)

            gate._on_release_timer()
            self.assertEqual(gate._continue_hold_count, 2)
            gate._on_release_timer()
            self.runtime_config.chat.add_item.assert_called_once()


class SilenceGatedOnlyTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_endpointing_for_tests()
        reset_heuristic_config_for_tests()
        reset_short_utterance_config_for_tests()
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
        reset_heuristic_config_for_tests()
        reset_short_utterance_config_for_tests()

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

    def test_sync_commit_suppressed_when_silence_gated_only(self) -> None:
        self.tracker.is_committed.return_value = False
        self.tracker.try_is_latest_after_reopen_grace.return_value = True

        with patch("buddy_tools.pulse.state.is_silence_gated_only_active", return_value=True):
            with patch("buddy_tools.voice.endpointing.perform_commit_side_effects") as side_effects:
                outputs = self._observe("Hello there")

        self.assertEqual(outputs, [])
        self.runtime_config.chat.add_item.assert_not_called()
        side_effects.assert_called_once()

    def test_sync_commit_normal_when_flag_off(self) -> None:
        self.tracker.is_committed.return_value = False
        self.tracker.try_is_latest_after_reopen_grace.return_value = True

        with patch("buddy_tools.pulse.state.is_silence_gated_only_active", return_value=False):
            outputs = self._observe("Hello there")

        self.assertEqual(len(outputs), 1)
        self.assertIsInstance(outputs[0], GenerateResponseRequest)
        self.runtime_config.chat.add_item.assert_called_once()

    def test_timer_commit_suppressed_when_silence_gated_only(self) -> None:
        self.tracker.is_committed.return_value = False
        self.tracker.try_is_latest_after_reopen_grace.side_effect = [None, True]
        self.tracker.has_pending_reopen_or_grace.return_value = False
        self.tracker.commit = Mock()

        with patch("buddy_tools.pulse.state.is_silence_gated_only_active", return_value=True):
            with patch("buddy_tools.voice.endpointing.perform_commit_side_effects") as side_effects:
                with patch.object(Timer, "start", lambda self: None):
                    outputs = self._observe("finish this thought")
                    self.assertEqual(outputs, [])

                get_endpointing_gate()._on_release_timer()

        self.assertTrue(self.queue.empty())
        self.runtime_config.chat.add_item.assert_not_called()
        side_effects.assert_called_once()
        self.tracker.commit.assert_called_once_with("t1", 0)

    def test_passthrough_suppressed_when_silence_gated_only(self) -> None:
        reset_endpointing_for_tests()
        controller = ListeningPauseController(should_listen=Event())

        with patch("buddy_tools.pulse.state.is_silence_gated_only_active", return_value=True):
            with patch("buddy_tools.voice.endpointing.perform_commit_side_effects") as side_effects:
                outputs = list(
                    process_transcription_with_listening_pause(
                        self.notifier,
                        Transcription(
                            text="immediate commit",
                            language_code=None,
                            turn_id="t1",
                            turn_revision=0,
                        ),
                        controller=controller,
                    )
                )

        self.assertEqual(outputs, [])
        self.runtime_config.chat.add_item.assert_not_called()
        side_effects.assert_called_once()


class ShortUtteranceCommitTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_endpointing_for_tests()
        reset_heuristic_config_for_tests()
        reset_short_utterance_config_for_tests()
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
        reset_heuristic_config_for_tests()
        reset_short_utterance_config_for_tests()

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

    def test_sync_discard_filler_skips_side_effects_and_chat(self) -> None:
        self.tracker.is_committed.return_value = False
        self.tracker.try_is_latest_after_reopen_grace.return_value = True

        with patch("buddy_tools.pulse.state.is_silence_gated_only_active", return_value=False):
            with patch("buddy_tools.voice.endpointing.perform_commit_side_effects") as side_effects:
                outputs = self._observe("yeah")

        self.assertEqual(outputs, [])
        self.runtime_config.chat.add_item.assert_not_called()
        side_effects.assert_not_called()

    def test_sync_discard_uh_skips_commit(self) -> None:
        self.tracker.is_committed.return_value = False
        self.tracker.try_is_latest_after_reopen_grace.return_value = True

        with patch("buddy_tools.pulse.state.is_silence_gated_only_active", return_value=False):
            with patch("buddy_tools.voice.endpointing.perform_commit_side_effects") as side_effects:
                outputs = self._observe("uh")

        self.assertEqual(outputs, [])
        self.runtime_config.chat.add_item.assert_not_called()
        side_effects.assert_not_called()

    def test_sync_multi_word_still_commits(self) -> None:
        self.tracker.is_committed.return_value = False
        self.tracker.try_is_latest_after_reopen_grace.return_value = True

        with patch("buddy_tools.pulse.state.is_silence_gated_only_active", return_value=False):
            outputs = self._observe("hello there")

        self.assertEqual(len(outputs), 1)
        self.assertIsInstance(outputs[0], GenerateResponseRequest)
        self.runtime_config.chat.add_item.assert_called_once()

    def test_timer_discard_filler_commits_tracker_without_llm(self) -> None:
        self.tracker.is_committed.return_value = False
        self.tracker.try_is_latest_after_reopen_grace.side_effect = [None, True]
        self.tracker.has_pending_reopen_or_grace.return_value = False
        self.tracker.commit = Mock()

        with patch("buddy_tools.pulse.state.is_silence_gated_only_active", return_value=False):
            with patch("buddy_tools.voice.endpointing.perform_commit_side_effects") as side_effects:
                with patch.object(Timer, "start", lambda self: None):
                    outputs = self._observe("yeah")
                    self.assertEqual(outputs, [])

                get_endpointing_gate()._on_release_timer()

        self.assertTrue(self.queue.empty())
        self.runtime_config.chat.add_item.assert_not_called()
        side_effects.assert_not_called()
        self.tracker.commit.assert_called_once_with("t1", 0)


if __name__ == "__main__":
    unittest.main()
