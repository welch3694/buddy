"""Tests for pulse session.yaml schema, rules, and template seeding."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from buddy_tools.pulse.rules import (
    apply_rule,
    apply_schedule_entry,
    evaluate_condition,
    evaluate_pulse_tick,
    resolve_mutation,
)
from buddy_tools.pulse.schema import (
    SessionValidationError,
    load_session_config,
    parse_session_config,
)
from buddy_tools.pulse.state import PulseState, build_pulse_state_from_session
from buddy_tools.skills import create_skill, execute_skill_tool

VALID_SESSION = """\
name: live-director

pulse:
  tick_interval_s: 5

init:
  set:
    phase: live
    current_camera: 1
    last_camera_switch_at: "$now"
    last_conversation_pulse_at: "$now"

cameras:
  - { id: 1, label: "wide shot" }
  - { id: 2, label: "close-up" }

rules:
  - id: camera-switch
    when: elapsed_since(last_camera_switch_at) >= 180
    set:
      current_camera: "$rotate(cameras)"
      last_camera_switch_at: "$now"
    cue: "Switch to camera {current_camera} — {label}."
    priority: mandatory

schedule:
  - at_s: 30
    cue: "Thirty second mark."
    priority: mandatory
    id: t30
"""


class PulseSchemaTests(unittest.TestCase):
    def test_parse_valid_session(self) -> None:
        import yaml

        config = parse_session_config(yaml.safe_load(VALID_SESSION), skill_name="live-director")
        self.assertEqual(config.name, "live-director")
        self.assertEqual(config.pulse.tick_interval_s, 5.0)
        self.assertEqual(config.init_set["phase"], "live")
        self.assertEqual(len(config.cameras), 2)
        self.assertEqual(config.rules[0].id, "camera-switch")
        self.assertEqual(config.schedule[0].entry_id, "t30")
        self.assertEqual(config.panel.senses, ())

    def test_panel_senses_round_trip(self) -> None:
        import yaml
        from buddy_tools.pulse.schema import session_config_to_dict

        raw = yaml.safe_load(VALID_SESSION)
        raw["panel"] = {"senses": ["phase", "pulse_mode", "current_camera", "pending_cue"]}
        config = parse_session_config(raw, skill_name="live-director")
        self.assertEqual(
            config.panel.senses,
            ("phase", "pulse_mode", "current_camera", "pending_cue"),
        )
        dumped = session_config_to_dict(config)
        self.assertEqual(
            dumped["panel"]["senses"],
            ["phase", "pulse_mode", "current_camera", "pending_cue"],
        )
        again = parse_session_config(dumped, skill_name="live-director")
        self.assertEqual(again.panel.senses, config.panel.senses)

    def test_rejects_invalid_panel_senses_entry(self) -> None:
        import yaml

        raw = yaml.safe_load(VALID_SESSION)
        raw["panel"] = {"senses": ["phase", 42]}
        with self.assertRaises(SessionValidationError):
            parse_session_config(raw, skill_name="live-director")

    def test_rejects_invalid_rule_without_when(self) -> None:
        import yaml

        raw = yaml.safe_load(VALID_SESSION)
        raw["rules"][0].pop("when")
        with self.assertRaises(SessionValidationError):
            parse_session_config(raw, skill_name="live-director")

    def test_legacy_flat_keys_normalized(self) -> None:
        import yaml

        raw = yaml.safe_load(
            "name: legacy\n"
            "tick_interval_seconds: 3\n"
            "phase: warmup\n"
            "rules: []\n"
            "schedule: []\n"
        )
        config = parse_session_config(raw, skill_name="legacy")
        self.assertEqual(config.pulse.tick_interval_s, 3.0)
        self.assertEqual(config.init_set["phase"], "warmup")

    def test_parse_silence_gated_only(self) -> None:
        import yaml

        raw = yaml.safe_load(
            "name: filming\n"
            "pulse:\n"
            "  silence_gated_only: true\n"
            "rules: []\n"
            "schedule: []\n"
        )
        config = parse_session_config(raw, skill_name="filming")
        self.assertTrue(config.pulse.silence_gated_only)

    def test_rejects_non_boolean_silence_gated_only(self) -> None:
        import yaml

        raw = yaml.safe_load(
            "name: bad\n"
            "pulse:\n"
            '  silence_gated_only: "maybe"\n'
            "rules: []\n"
            "schedule: []\n"
        )
        with self.assertRaises(SessionValidationError):
            parse_session_config(raw, skill_name="bad")

    def test_parse_scene_capture_conversational(self) -> None:
        import yaml

        raw = yaml.safe_load(
            "name: director\n"
            "pulse:\n"
            "  scene_capture: conversational\n"
            "rules: []\n"
            "schedule: []\n"
        )
        config = parse_session_config(raw, skill_name="director")
        self.assertEqual(config.pulse.scene_capture, "conversational")

    def test_scene_capture_defaults_to_off(self) -> None:
        import yaml

        config = parse_session_config(
            yaml.safe_load("name: x\nrules: []\nschedule: []\n"),
            skill_name="x",
        )
        self.assertEqual(config.pulse.scene_capture, "off")

    def test_rejects_invalid_scene_capture(self) -> None:
        import yaml

        raw = yaml.safe_load(
            "name: bad\n"
            "pulse:\n"
            "  scene_capture: always\n"
            "rules: []\n"
            "schedule: []\n"
        )
        with self.assertRaises(SessionValidationError):
            parse_session_config(raw, skill_name="bad")


class PulseInitSetTests(unittest.TestCase):
    def test_init_set_evaluates_now_mutation(self) -> None:
        import yaml

        config = parse_session_config(
            yaml.safe_load(
                "name: t\n"
                "init:\n"
                "  set:\n"
                '    last_camera_switch_at: "$now"\n'
                "    switch_interval_s: 180\n"
                "rules: []\n"
                "schedule: []\n"
            ),
            skill_name="t",
        )
        state = build_pulse_state_from_session("t", config)
        self.assertEqual(state.vars["last_camera_switch_at"], state.started_at)
        self.assertNotEqual(state.vars["last_camera_switch_at"], "$now")

    def test_init_set_evaluates_numeric_mutation_in_order(self) -> None:
        import yaml

        config = parse_session_config(
            yaml.safe_load(
                "name: t\n"
                "init:\n"
                "  set:\n"
                "    base: 100\n"
                '    adjusted: "$add(base, 5)"\n'
                "rules: []\n"
                "schedule: []\n"
            ),
            skill_name="t",
        )
        state = build_pulse_state_from_session("t", config)
        self.assertEqual(state.vars["base"], 100)
        self.assertEqual(state.vars["adjusted"], 105)


class PulseRuleEngineTests(unittest.TestCase):
    def _session(self):
        import yaml

        return parse_session_config(yaml.safe_load(VALID_SESSION), skill_name="live-director")

    def _state(self) -> PulseState:
        state = build_pulse_state_from_session("live-director", self._session())
        state.vars["last_camera_switch_at"] = (
            datetime.now(UTC) - timedelta(seconds=200)
        ).replace(microsecond=0).isoformat()
        return state

    def test_elapsed_since_condition(self) -> None:
        state = self._state()
        self.assertTrue(
            evaluate_condition(state, "elapsed_since(last_camera_switch_at) >= 180")
        )

    def test_phase_equality(self) -> None:
        state = self._state()
        self.assertTrue(evaluate_condition(state, "phase == live"))
        self.assertFalse(evaluate_condition(state, "phase == warmup"))

    def test_session_elapsed_condition(self) -> None:
        state = self._state()
        state.started_at = (
            datetime.now(UTC) - timedelta(seconds=2000)
        ).replace(microsecond=0).isoformat()
        self.assertTrue(evaluate_condition(state, "session_elapsed >= 1800"))
        self.assertFalse(evaluate_condition(state, "session_elapsed >= 3600"))

    def test_elapsed_since_var_threshold(self) -> None:
        state = self._state()
        state.vars["switch_interval_s"] = 120
        state.vars["last_camera_switch_at"] = (
            datetime.now(UTC) - timedelta(seconds=130)
        ).replace(microsecond=0).isoformat()
        self.assertTrue(
            evaluate_condition(state, "elapsed_since(last_camera_switch_at) >= switch_interval_s")
        )
        state.vars["switch_interval_s"] = 180
        self.assertFalse(
            evaluate_condition(state, "elapsed_since(last_camera_switch_at) >= switch_interval_s")
        )

    def test_compound_and_condition(self) -> None:
        state = self._state()
        state.phase = "late"
        state.vars["switch_interval_s"] = 120
        self.assertTrue(
            evaluate_condition(
                state,
                "phase == late && elapsed_since(last_camera_switch_at) >= switch_interval_s",
            )
        )
        state.phase = "early"
        self.assertFalse(
            evaluate_condition(
                state,
                "phase == late && elapsed_since(last_camera_switch_at) >= switch_interval_s",
            )
        )

    def test_adaptive_pace_tightens_after_session_elapsed(self) -> None:
        import yaml

        session_yaml = yaml.safe_load(
            """
name: live-director
pulse:
  tick_interval_s: 5
init:
  set:
    phase: live
    current_camera: 1
    switch_interval_s: 180
cameras:
  - { id: 1, label: "wide shot" }
  - { id: 2, label: "close-up" }
rules:
  - id: tighten-pace
    when: session_elapsed >= 1800
    once: true
    set:
      switch_interval_s: 120
      last_camera_switch_at: "$now"
  - id: camera-switch
    when: elapsed_since(last_camera_switch_at) >= switch_interval_s
    set:
      current_camera: "$rotate(cameras)"
      last_camera_switch_at: "$now"
    cue: "Switch to camera {current_camera} — {label}."
    priority: mandatory
  - id: tighten-pace
    when: session_elapsed >= 1800
    once: true
    set:
      switch_interval_s: 120
      last_camera_switch_at: "$now"
schedule: []
"""
        )
        session = parse_session_config(session_yaml, skill_name="live-director")
        state = build_pulse_state_from_session("live-director", session)
        past = (datetime.now(UTC) - timedelta(seconds=2000)).replace(microsecond=0).isoformat()
        state.started_at = past
        state.vars["last_camera_switch_at"] = past

        evaluate_pulse_tick(state, session)
        self.assertEqual(state.vars["switch_interval_s"], 120)
        self.assertIsNone(state.pending_cue)

        state.vars["last_camera_switch_at"] = (
            datetime.now(UTC) - timedelta(seconds=130)
        ).replace(microsecond=0).isoformat()
        evaluate_pulse_tick(state, session)
        self.assertIsNotNone(state.pending_cue)
        self.assertIn("camera", (state.pending_cue or "").lower())

    def test_numeric_mutations(self) -> None:
        state = self._state()
        session = self._session()
        state.vars["switch_interval_s"] = 180
        self.assertEqual(resolve_mutation("$add(switch_interval_s, 10)", state, session), 190)
        self.assertEqual(resolve_mutation("$sub(switch_interval_s, 5)", state, session), 175)
        self.assertEqual(resolve_mutation("$min(switch_interval_s, 60)", state, session), 60)
        self.assertEqual(resolve_mutation("$max(switch_interval_s, 200)", state, session), 200)
        self.assertEqual(resolve_mutation("$clamp(50, 60)", state, session), 60)
        self.assertEqual(resolve_mutation("$clamp(150, 60, 120)", state, session), 120)

    def test_nested_clamp_sub_mutation(self) -> None:
        state = self._state()
        session = self._session()
        state.vars["switch_interval_s"] = 180
        state.vars["min_switch_interval_s"] = 60
        resolved = resolve_mutation(
            "$clamp($sub(switch_interval_s, 5), min_switch_interval_s)",
            state,
            session,
        )
        self.assertEqual(resolved, 175)

    def test_progressive_tighten_on_repeated_camera_switch(self) -> None:
        import yaml

        session = parse_session_config(
            yaml.safe_load(
                """
name: live-director
pulse:
  tick_interval_s: 5
init:
  set:
    phase: live
    current_camera: 1
    switch_interval_s: 180
    min_switch_interval_s: 60
    tighten_step_s: 5
cameras:
  - { id: 1, label: "wide shot" }
  - { id: 2, label: "close-up" }
rules:
  - id: camera-switch
    when: elapsed_since(last_camera_switch_at) >= switch_interval_s
    set:
      current_camera: "$rotate(cameras)"
      last_camera_switch_at: "$now"
      switch_interval_s: "$clamp($sub(switch_interval_s, tighten_step_s), min_switch_interval_s)"
    cue: "Switch to camera {current_camera} — {label}."
    priority: mandatory
schedule: []
"""
            ),
            skill_name="live-director",
        )
        state = build_pulse_state_from_session("live-director", session)
        past = (datetime.now(UTC) - timedelta(seconds=200)).replace(microsecond=0).isoformat()
        state.vars["last_camera_switch_at"] = past
        rule = session.rules[0]

        apply_rule(state, rule, session)
        self.assertEqual(state.vars["switch_interval_s"], 175)

        state.vars["last_camera_switch_at"] = past
        apply_rule(state, rule, session)
        self.assertEqual(state.vars["switch_interval_s"], 170)

        state.vars["switch_interval_s"] = 62
        state.vars["last_camera_switch_at"] = past
        apply_rule(state, rule, session)
        self.assertEqual(state.vars["switch_interval_s"], 60)

        state.vars["last_camera_switch_at"] = past
        apply_rule(state, rule, session)
        self.assertEqual(state.vars["switch_interval_s"], 60)

    def test_camera_switch_rule_fires_from_session_start(self) -> None:
        from datetime import timedelta

        from buddy_tools.pulse.rules import evaluate_pulse_tick

        session = self._session()
        state = build_pulse_state_from_session("live-director", session)
        self.assertIn("last_camera_switch_at", state.vars)

        past = (datetime.now(UTC) - timedelta(seconds=200)).replace(microsecond=0).isoformat()
        state.started_at = past
        state.vars["last_camera_switch_at"] = past
        evaluate_pulse_tick(state, session)
        self.assertIsNotNone(state.pending_cue)
        self.assertIn("camera", state.pending_cue.lower())

    def test_rotate_and_cue_interpolation(self) -> None:
        session = self._session()
        state = self._state()
        rule = session.rules[0]
        fired = apply_rule(state, rule, session)
        self.assertTrue(fired)
        self.assertEqual(state.vars["current_camera"], 2)
        self.assertIn("close-up", state.pending_cue or "")

    def test_once_rule_not_refired(self) -> None:
        session = self._session()
        state = self._state()
        once_rule = replace(session.rules[0], once=True)
        self.assertTrue(apply_rule(state, once_rule, session))
        self.assertFalse(apply_rule(state, once_rule, session))
        self.assertIn("camera-switch", state.fired_rules)

    def test_schedule_fires_at_elapsed(self) -> None:
        session = self._session()
        state = build_pulse_state_from_session("live-director", session)
        state.started_at = (datetime.now(UTC) - timedelta(seconds=45)).replace(microsecond=0).isoformat()
        evaluate_pulse_tick(state, session)
        self.assertEqual(state.pending_cue, "Thirty second mark.")
        self.assertIn("schedule:t30", state.fired_rules)


class PulseMandatoryCueMergeTests(unittest.TestCase):
    def _session(self):
        import yaml

        return parse_session_config(
            yaml.safe_load(
                """
name: multi-cue
pulse:
  tick_interval_s: 5
init:
  set:
    phase: live
rules:
  - id: button-cue
    when: elapsed_since(last_button_cue_at) >= 60
    set:
      last_button_cue_at: "$now"
    cue: "Hit the button."
    priority: mandatory
  - id: camera-switch
    when: elapsed_since(last_camera_cue_at) >= 60
    set:
      current_camera: 2
      last_camera_cue_at: "$now"
    cue: "Switch to camera 2."
    priority: mandatory
  - id: filler
    when: elapsed_since(last_conversation_pulse_at) >= 10
    set:
      last_conversation_pulse_at: "$now"
    cue: "Say something casual."
    priority: conversational
schedule:
  - at_s: 30
    cue: "Thirty second mark."
    priority: mandatory
    id: t30
"""
            ),
            skill_name="multi-cue",
        )

    def test_two_mandatory_rules_same_tick_merge_cues(self) -> None:
        session = self._session()
        state = build_pulse_state_from_session("multi-cue", session)
        past = (datetime.now(UTC) - timedelta(seconds=90)).replace(microsecond=0).isoformat()
        state.vars["last_button_cue_at"] = past
        state.vars["last_camera_cue_at"] = past

        evaluate_pulse_tick(state, session)

        self.assertEqual(state.pending_cue, "Hit the button.; Switch to camera 2.")
        self.assertEqual(state.cue_priority, "mandatory")
        self.assertEqual(state.vars["current_camera"], 2)

    def test_mandatory_rule_appends_while_prior_cue_pending(self) -> None:
        session = self._session()
        state = build_pulse_state_from_session("multi-cue", session)
        first_since = (datetime.now(UTC) - timedelta(seconds=5)).replace(microsecond=0).isoformat()
        state.pending_cue = "Hit the button."
        state.pending_cue_since = first_since
        state.cue_priority = "mandatory"
        state.vars["last_camera_cue_at"] = (
            datetime.now(UTC) - timedelta(seconds=90)
        ).replace(microsecond=0).isoformat()

        apply_rule(state, session.rules[1], session)

        self.assertEqual(state.pending_cue, "Hit the button.; Switch to camera 2.")
        self.assertEqual(state.pending_cue_since, first_since)

    def test_mandatory_append_deduplicates_identical_cue(self) -> None:
        session = self._session()
        state = build_pulse_state_from_session("multi-cue", session)
        first_since = (datetime.now(UTC) - timedelta(seconds=5)).replace(microsecond=0).isoformat()
        state.pending_cue = "Hit the button."
        state.pending_cue_since = first_since
        state.cue_priority = "mandatory"
        state.vars["last_button_cue_at"] = (
            datetime.now(UTC) - timedelta(seconds=90)
        ).replace(microsecond=0).isoformat()

        apply_rule(state, session.rules[0], session)

        self.assertEqual(state.pending_cue, "Hit the button.")
        self.assertEqual(state.pending_cue_since, first_since)

    def test_schedule_entries_merge_mandatory_cues(self) -> None:
        session = self._session()
        state = build_pulse_state_from_session("multi-cue", session)
        first_since = (datetime.now(UTC) - timedelta(seconds=5)).replace(microsecond=0).isoformat()
        state.pending_cue = "Hit the button."
        state.pending_cue_since = first_since
        state.cue_priority = "mandatory"

        apply_schedule_entry(state, session.schedule[0], session, elapsed_s=45.0)

        self.assertEqual(state.pending_cue, "Hit the button.; Thirty second mark.")
        self.assertEqual(state.pending_cue_since, first_since)
        self.assertIn("schedule:t30", state.fired_rules)

    def test_conversational_does_not_merge_with_mandatory_pending(self) -> None:
        session = self._session()
        state = build_pulse_state_from_session("multi-cue", session)
        first_since = (datetime.now(UTC) - timedelta(seconds=5)).replace(microsecond=0).isoformat()
        state.pending_cue = "Hit the button."
        state.pending_cue_since = first_since
        state.cue_priority = "mandatory"
        state.vars["last_conversation_pulse_at"] = (
            datetime.now(UTC) - timedelta(seconds=20)
        ).replace(microsecond=0).isoformat()

        apply_rule(state, session.rules[2], session)

        self.assertEqual(state.pending_cue, "Hit the button.")
        self.assertEqual(state.cue_priority, "mandatory")
        self.assertEqual(state.pending_cue_since, first_since)


class PulseTemplateSeedingTests(unittest.TestCase):
    def setUp(self) -> None:
        from buddy_tools import personality as personality_module
        import buddy_tools.voice.voices as voices_module
        from buddy_tools.infra.bootstrap import get_memory_root, set_memory_root
        from buddy_tools.infra.data_dir import reset_data_dir_config
        from buddy_tools.personality import create_personality, set_active_personality, set_personalities_dir
        from buddy_tools.voice.voices import set_voices_dir

        self._original_personalities_dir = personality_module.get_personalities_dir()
        self._original_voices_dir = voices_module.get_voices_dir()
        self._original_memory_root = get_memory_root()

        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.repo_root = self.root / "repo"
        self.personalities_root = self.root / "personalities"
        self.voices_root = self.root / "voices"
        self.memory_root = self.root / "memory"
        self.repo_root.mkdir()
        (self.repo_root / "skills").mkdir()
        self.personalities_root.mkdir()
        self.voices_root.mkdir()
        self.memory_root.mkdir()

        reset_data_dir_config(repo_root=self.repo_root, data_dir=self.root / "data")
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
        from buddy_tools.infra.bootstrap import set_memory_root
        from buddy_tools.infra.data_dir import reset_data_dir_config
        from buddy_tools.personality import set_personalities_dir
        from buddy_tools.voice.voices import set_voices_dir

        reset_data_dir_config()
        set_personalities_dir(self._original_personalities_dir)
        set_voices_dir(self._original_voices_dir)
        if self._original_memory_root is not None:
            set_memory_root(self._original_memory_root)
        self._tmpdir.cleanup()

    def test_create_pulse_skill_seeds_session_yaml(self) -> None:
        skill = create_skill(
            "my-pulse",
            "A pulse workflow.",
            "# My pulse\n\nNarrate cues.",
            skill_type="pulse",
        )
        session_path = skill.directory / "references" / "session.yaml"
        self.assertTrue(session_path.is_file())
        content = session_path.read_text(encoding="utf-8")
        self.assertIn("camera-switch", content)
        self.assertIn("name: my-pulse", content)
        config = load_session_config(skill.directory, skill_name=skill.name)
        self.assertEqual(config.name, "my-pulse")

    def test_start_skill_rejects_invalid_session_yaml(self) -> None:
        skill = create_skill(
            "bad-pulse",
            "Broken pulse.",
            "# Bad pulse",
            skill_type="pulse",
        )
        session_path = skill.directory / "references" / "session.yaml"
        session_path.write_text("name: bad-pulse\nrules: not-a-list\n", encoding="utf-8")

        result = execute_skill_tool(self.memory_root, "coach", "start_skill", {"name": "bad-pulse"})
        self.assertIn("Error", result.output)


if __name__ == "__main__":
    unittest.main()
