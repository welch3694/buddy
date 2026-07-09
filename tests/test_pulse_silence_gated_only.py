"""Tests for silence_gated_only pulse mode (issue #91)."""

from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from queue import Queue
from threading import Event

import yaml
from openai.types.realtime.realtime_response_create_params import RealtimeResponseCreateParams

from buddy_tools import personality as personality_module
import buddy_tools.voice.voices as voices_module
from buddy_tools.infra.bootstrap import get_memory_root, set_memory_root
from buddy_tools.infra.data_dir import reset_data_dir_config
from buddy_tools.personality import create_personality, set_active_personality, set_personalities_dir
from buddy_tools.pulse.gates import (
    conversational_pulse_gates_allow,
    directed_pulse_gates_allow,
    reset_pulse_gates_for_tests,
    select_pulse_mode,
    set_last_user_speech_stopped_at,
    set_perf_counter_for_tests,
)
from buddy_tools.pulse.inject import inject_pulse_turn, reset_pulse_inject_for_tests
from buddy_tools.pulse.schema import parse_session_config
from buddy_tools.pulse.state import (
    PulseState,
    build_pulse_state_from_session,
    is_silence_gated_only_active,
    load_pulse_state,
    save_pulse_state,
)
from buddy_tools.skills import execute_skill_tool
from buddy_tools.voice.voices import set_voices_dir
from speech_to_speech.LLM.chat import Chat
from speech_to_speech.api.openai_realtime.runtime_config import RuntimeConfig

SESSION_YAML_SILENCE_GATED = """\
name: live-director
pulse:
  tick_interval_s: 5
  conversation_check_s: 10
  min_speak_interval_s: 5
  mandatory_cue_max_defer_s: 2
  silence_gated_only: true
init:
  set:
    phase: live
rules: []
schedule: []
"""


class SilenceGatedOnlyHelperTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_data_dir_config()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.personalities_root = self.root / "personalities"
        self.voices_root = self.root / "voices"
        self.memory_root = self.root / "memory"
        for path in (self.personalities_root, self.voices_root, self.memory_root):
            path.mkdir(parents=True)

        set_personalities_dir(self.personalities_root)
        set_voices_dir(self.voices_root)
        set_memory_root(self.memory_root)

        voice_dir = self.voices_root / "cliff"
        voice_dir.mkdir(parents=True)
        (voice_dir / "audio.wav").write_bytes(b"RIFF")
        (voice_dir / "ref_text.txt").write_text("cliff transcript", encoding="utf-8")
        create_personality("coach", "Coach", "You are Coach.", voice_id="cliff")
        set_active_personality("coach")

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        reset_data_dir_config()

    def test_is_silence_gated_only_active_when_pulse_running(self) -> None:
        session = parse_session_config(
            yaml.safe_load(SESSION_YAML_SILENCE_GATED),
            skill_name="live-director",
        )
        state = build_pulse_state_from_session("live-director", session)
        save_pulse_state(self.memory_root, "coach", state)

        self.assertTrue(is_silence_gated_only_active())

    def test_is_silence_gated_only_inactive_without_pulse(self) -> None:
        self.assertFalse(is_silence_gated_only_active())


class SilenceGatedOnlyPulsePathTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_pulse_gates_for_tests()
        reset_pulse_inject_for_tests()
        self.session = parse_session_config(
            yaml.safe_load(SESSION_YAML_SILENCE_GATED),
            skill_name="live-director",
        )
        self.state = build_pulse_state_from_session("live-director", self.session)
        self.should_listen = Event()
        self.should_listen.set()
        set_perf_counter_for_tests(lambda: 1000.0)
        set_last_user_speech_stopped_at(990.0)
        self._tmpdir = tempfile.TemporaryDirectory()
        self.memory_root = Path(self._tmpdir.name) / "memory"
        self.memory_root.mkdir()
        self.queue: Queue = Queue()
        self.runtime_config = RuntimeConfig()
        self.runtime_config.chat = Chat(4)
        self.runtime_config.session.instructions = "Base system prompt."

    def tearDown(self) -> None:
        reset_pulse_gates_for_tests()
        reset_pulse_inject_for_tests()
        self._tmpdir.cleanup()

    def test_mandatory_cue_still_injects_with_silence_gated_only(self) -> None:
        self.state.pending_cue = "Switch camera."
        self.state.cue_priority = "mandatory"
        self.state.pending_cue_since = datetime.now(UTC).replace(microsecond=0).isoformat()
        self.assertTrue(
            directed_pulse_gates_allow(self.state, self.session, should_listen=self.should_listen)
        )
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

    def test_conversational_pulse_still_allowed_with_silence_gated_only(self) -> None:
        anchor = (datetime.now(UTC) - timedelta(seconds=61)).replace(microsecond=0).isoformat()
        self.state.vars["last_conversation_pulse_at"] = anchor
        self.state.last_assistant_speech_at = anchor
        self.assertTrue(
            conversational_pulse_gates_allow(self.state, self.session, should_listen=self.should_listen)
        )
        self.assertEqual(
            select_pulse_mode(self.state, self.session, should_listen=self.should_listen),
            "conversational",
        )

    def test_narrator_muted_still_blocks_injection_with_silence_gated_only(self) -> None:
        self.state.pending_cue = "Switch camera."
        self.state.cue_priority = "mandatory"
        self.state.narrator_muted = True
        self.assertFalse(
            directed_pulse_gates_allow(self.state, self.session, should_listen=self.should_listen)
        )


class SilenceGatedOnlyIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_pulse_inject_for_tests()
        self._original_personalities_dir = personality_module.get_personalities_dir()
        self._original_voices_dir = voices_module.get_voices_dir()
        self._original_memory_root = get_memory_root()

        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.repo_root = self.root / "repo"
        self.data_dir = self.root / "data"
        self.personalities_root = self.data_dir / "personalities"
        self.voices_root = self.root / "voices"
        self.memory_root = self.data_dir / "memory"
        self.builtin_skills_root = self.repo_root / "skills"

        for path in (
            self.personalities_root,
            self.voices_root,
            self.memory_root,
            self.builtin_skills_root,
        ):
            path.mkdir(parents=True)

        reset_data_dir_config(repo_root=self.repo_root, data_dir=self.data_dir)
        set_personalities_dir(self.personalities_root)
        set_voices_dir(self.voices_root)
        set_memory_root(self.memory_root)

        voice_dir = self.voices_root / "cliff"
        voice_dir.mkdir(parents=True)
        (voice_dir / "audio.wav").write_bytes(b"RIFF")
        (voice_dir / "ref_text.txt").write_text("cliff transcript", encoding="utf-8")
        create_personality("coach", "Coach", "You are Coach.", voice_id="cliff")
        set_active_personality("coach")

    def tearDown(self) -> None:
        reset_pulse_inject_for_tests()
        reset_data_dir_config()
        set_personalities_dir(self._original_personalities_dir)
        set_voices_dir(self._original_voices_dir)
        set_memory_root(self._original_memory_root)
        self._tmpdir.cleanup()

    def test_start_skill_snapshots_silence_gated_only(self) -> None:
        from buddy_tools.skills import create_skill

        skill = create_skill(
            "filming-director",
            "Filming director.",
            "# Filming",
            skill_type="pulse",
        )
        session_path = skill.directory / "references" / "session.yaml"
        raw = yaml.safe_load(session_path.read_text(encoding="utf-8"))
        raw["pulse"]["silence_gated_only"] = True
        session_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

        result = execute_skill_tool(
            self.memory_root,
            "coach",
            "start_skill",
            {"name": "filming-director"},
        )
        self.assertNotIn("Error", result.output)

        state = load_pulse_state(self.memory_root, "coach")
        assert state is not None
        session = state.get_session_config()
        assert session is not None
        self.assertTrue(session.pulse.silence_gated_only)
        self.assertTrue(is_silence_gated_only_active())


if __name__ == "__main__":
    unittest.main()
