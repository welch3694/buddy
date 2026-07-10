"""Tests for pulse gating, injection, and pipeline hooks."""

from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from queue import Queue
from threading import Event

import yaml
from openai.types.realtime.realtime_response_create_params import RealtimeResponseCreateParams

from buddy_tools.voice.listening_pause import get_listening_pause_controller
from buddy_tools.pulse.gates import (
    conversational_pulse_gates_allow,
    directed_pulse_gates_allow,
    reset_pulse_gates_for_tests,
    select_pulse_mode,
    set_last_user_speech_stopped_at,
    set_perf_counter_for_tests,
)
from buddy_tools.pulse.inject import (
    NO_OUTPUT_MARKER,
    build_conversational_pulse_instructions,
    build_directed_pulse_instructions,
    handle_pulse_end_of_response,
    handle_pulse_response_chunk,
    inject_pulse_turn,
    is_no_output_text,
    reset_pulse_inject_for_tests,
)
from buddy_tools.pulse.rules import evaluate_pulse_tick
from buddy_tools.pulse.schema import parse_session_config
from buddy_tools.pulse.state import PulseState, build_pulse_state_from_session, load_pulse_state, save_pulse_state
from speech_to_speech.LLM.chat import Chat
from speech_to_speech.api.openai_realtime.runtime_config import RuntimeConfig
from speech_to_speech.pipeline.messages import LLMResponseChunk

SESSION_YAML = """\
name: live-director
pulse:
  tick_interval_s: 5
  conversation_check_s: 10
  min_speak_interval_s: 5
  mandatory_cue_max_defer_s: 2
init:
  set:
    phase: live
rules: []
schedule: []
"""


class PulseGateTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_pulse_gates_for_tests()
        get_listening_pause_controller().paused = False
        self.session = parse_session_config(yaml.safe_load(SESSION_YAML), skill_name="live-director")
        self.state = build_pulse_state_from_session("live-director", self.session)
        self.should_listen = Event()
        self.should_listen.set()
        set_perf_counter_for_tests(lambda: 1000.0)
        set_last_user_speech_stopped_at(990.0)

    def tearDown(self) -> None:
        reset_pulse_gates_for_tests()

    def test_directed_waits_for_silence(self) -> None:
        self.state.pending_cue = "Switch camera."
        self.state.cue_priority = "mandatory"
        self.state.pending_cue_since = datetime.now(UTC).replace(microsecond=0).isoformat()
        set_last_user_speech_stopped_at(999.0)
        self.assertFalse(
            directed_pulse_gates_allow(self.state, self.session, should_listen=self.should_listen)
        )

    def test_directed_fires_after_silence(self) -> None:
        self.state.pending_cue = "Switch camera."
        self.state.cue_priority = "mandatory"
        self.state.pending_cue_since = datetime.now(UTC).replace(microsecond=0).isoformat()
        self.assertTrue(
            directed_pulse_gates_allow(self.state, self.session, should_listen=self.should_listen)
        )

    def test_directed_force_fire_after_max_defer(self) -> None:
        self.state.pending_cue = "Switch camera."
        self.state.cue_priority = "mandatory"
        self.state.pending_cue_since = (
            datetime.now(UTC) - timedelta(seconds=5)
        ).replace(microsecond=0).isoformat()
        set_last_user_speech_stopped_at(999.0)
        self.assertTrue(
            directed_pulse_gates_allow(self.state, self.session, should_listen=self.should_listen)
        )

    def test_directed_skips_when_narrator_muted(self) -> None:
        self.state.pending_cue = "Switch camera."
        self.state.cue_priority = "mandatory"
        self.state.narrator_muted = True
        self.assertFalse(
            directed_pulse_gates_allow(self.state, self.session, should_listen=self.should_listen)
        )

    def test_conversational_requires_no_mandatory_pending(self) -> None:
        self.state.pending_cue = "Mandatory cue"
        self.state.cue_priority = "mandatory"
        self.assertFalse(
            conversational_pulse_gates_allow(self.state, self.session, should_listen=self.should_listen)
        )

    def test_select_prefers_directed_over_conversational(self) -> None:
        self.state.pending_cue = "Switch camera."
        self.state.cue_priority = "mandatory"
        self.state.pending_cue_since = datetime.now(UTC).replace(microsecond=0).isoformat()
        self.assertEqual(
            select_pulse_mode(self.state, self.session, should_listen=self.should_listen),
            "directed",
        )

    def test_conversational_allowed_after_interval_when_no_pending_cue(self) -> None:
        anchor = (datetime.now(UTC) - timedelta(seconds=61)).replace(microsecond=0).isoformat()
        self.state.vars["last_conversation_pulse_at"] = anchor
        self.state.last_assistant_speech_at = anchor
        self.assertEqual(
            select_pulse_mode(self.state, self.session, should_listen=self.should_listen),
            "conversational",
        )

    def test_conversation_check_rule_blocks_same_tick_inject(self) -> None:
        """A rule that resets last_conversation_pulse_at prevents gate-based inject."""
        session = parse_session_config(
            yaml.safe_load(
                SESSION_YAML.replace("rules: []", "")
                + """
rules:
  - id: conversation-check
    when: elapsed_since(last_conversation_pulse_at) >= 10
    once: false
    set:
      last_conversation_pulse_at: "$now"
    cue: ""
    priority: conversational
"""
            ),
            skill_name="live-director",
        )
        anchor = (datetime.now(UTC) - timedelta(seconds=11)).replace(microsecond=0).isoformat()
        state = build_pulse_state_from_session("live-director", session)
        state.vars["last_conversation_pulse_at"] = anchor
        state.last_assistant_speech_at = anchor

        evaluate_pulse_tick(state, session)
        self.assertIsNone(
            select_pulse_mode(state, session, should_listen=self.should_listen),
        )


class PulseInjectTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_pulse_inject_for_tests()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.memory_root = Path(self._tmpdir.name) / "memory"
        self.memory_root.mkdir()
        self.session = parse_session_config(yaml.safe_load(SESSION_YAML), skill_name="live-director")
        self.state = build_pulse_state_from_session("live-director", self.session)
        self.state.pending_cue = "Switch to camera 2."
        self.state.cue_priority = "mandatory"
        self.queue: Queue = Queue()
        self.runtime_config = RuntimeConfig()
        self.runtime_config.chat = Chat(4)
        self.runtime_config.session.instructions = "Base system prompt."

    def tearDown(self) -> None:
        reset_pulse_inject_for_tests()
        self._tmpdir.cleanup()

    def test_directed_instructions_include_pending_cue_and_snapshot(self) -> None:
        instructions = build_directed_pulse_instructions(self.state, "Base.")
        self.assertIn("Switch to camera 2.", instructions)
        self.assertIn("Pending cue(s):", instructions)
        self.assertIn("deliver all pending cues", instructions)
        self.assertIn("Pulse state snapshot", instructions)
        self.assertIn("Do not call tools", instructions)

    def test_conversational_instructions_include_no_output(self) -> None:
        instructions = build_conversational_pulse_instructions(self.state, "Base.")
        self.assertIn(NO_OUTPUT_MARKER, instructions)

    def test_inject_queues_generate_response_request(self) -> None:
        injected = inject_pulse_turn(
            memory_root=self.memory_root,
            persona_namespace="coach",
            state=self.state,
            mode="directed",
            text_prompt_queue=self.queue,
            runtime_config=self.runtime_config,
        )
        self.assertTrue(injected)
        req = self.queue.get_nowait()
        self.assertIsNotNone(req.response)
        assert isinstance(req.response, RealtimeResponseCreateParams)
        self.assertIn("pending cue", req.response.instructions.lower())

    def test_no_output_detection(self) -> None:
        self.assertTrue(is_no_output_text("[NO_OUTPUT]"))
        self.assertFalse(is_no_output_text("Hello there"))

    def test_handle_pulse_chunk_suppresses_no_output(self) -> None:
        inject_pulse_turn(
            memory_root=self.memory_root,
            persona_namespace="coach",
            state=self.state,
            mode="conversational",
            text_prompt_queue=self.queue,
            runtime_config=self.runtime_config,
        )
        self.queue.get_nowait()
        chunk = LLMResponseChunk(text=NO_OUTPUT_MARKER, turn_id="t1", turn_revision=0)
        self.assertIsNone(handle_pulse_response_chunk(chunk))

    def test_directed_completion_clears_pending_cue(self) -> None:
        self.state.pending_cue = "Go live."
        save_pulse_state(self.memory_root, "coach", self.state)
        inject_pulse_turn(
            memory_root=self.memory_root,
            persona_namespace="coach",
            state=self.state,
            mode="directed",
            text_prompt_queue=self.queue,
            runtime_config=self.runtime_config,
        )
        self.queue.get_nowait()
        handle_pulse_response_chunk(
            LLMResponseChunk(text="Switch to camera two.", turn_id="t1", turn_revision=0)
        )
        handle_pulse_end_of_response()

        loaded = load_pulse_state(self.memory_root, "coach")
        assert loaded is not None
        self.assertIsNone(loaded.pending_cue)
        self.assertFalse(loaded.pulse_in_flight)


if __name__ == "__main__":
    unittest.main()
