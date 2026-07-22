"""Tests for write_skill_file, read_skill_file by name, and update_pulse_config."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

from buddy_tools import personality as personality_module
import buddy_tools.voice.voices as voices_module
from buddy_tools.core.registry import ALL_TOOL_DEFINITIONS, build_tool_instructions
from buddy_tools.infra.bootstrap import get_memory_root, set_memory_root
from buddy_tools.infra.data_dir import reset_data_dir_config
from buddy_tools.personality import create_personality, set_active_personality, set_personalities_dir
from buddy_tools.pulse.config_merge import apply_pulse_config, merge_pulse_params
from buddy_tools.pulse.schema import load_session_config
from buddy_tools.skills import create_skill, execute_skill_tool
from buddy_tools.voice.voices import set_voices_dir

CHECKLIST_SKILL_BODY = """\
---
name: equipment-setup
description: Rig checks.
metadata:
  buddy:
    type: checklist
---

# Equipment setup

## Steps

### mic
Check the microphone.
"""

BUILTIN_SKILL = """\
---
name: edit-personality
description: Edit personality.
metadata:
  buddy:
    type: generic
---

# Edit personality
"""


class PulseConfigToolsTests(unittest.TestCase):
    def setUp(self) -> None:
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

        skill_dir = self.personalities_root / "coach" / "skills" / "equipment-setup"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(CHECKLIST_SKILL_BODY, encoding="utf-8")
        refs = skill_dir / "references"
        refs.mkdir()
        (refs / "details.md").write_text("Extra rig details here.", encoding="utf-8")

        builtin_dir = self.builtin_skills_root / "edit-personality"
        builtin_dir.mkdir(parents=True)
        (builtin_dir / "SKILL.md").write_text(BUILTIN_SKILL, encoding="utf-8")

    def tearDown(self) -> None:
        reset_data_dir_config()
        set_personalities_dir(self._original_personalities_dir)
        set_voices_dir(self._original_voices_dir)
        if self._original_memory_root is not None:
            set_memory_root(self._original_memory_root)
        self._tmpdir.cleanup()

    def test_registry_includes_new_tools(self) -> None:
        names = {tool.name for tool in ALL_TOOL_DEFINITIONS}
        self.assertIn("skill", names)

    def test_build_tool_instructions_mentions_new_tools(self) -> None:
        text = build_tool_instructions(
            "Coach prompt.",
            "(no memory saved yet)",
            memory_root=self.memory_root,
            persona_namespace="coach",
            personality_id="coach",
        )
        self.assertIn("skill(action=write_file)", text)
        self.assertIn("skill(action=update_pulse_config)", text)

    def test_write_and_read_roundtrip(self) -> None:
        skill = create_skill(
            "notes-skill",
            "A skill with notes.",
            "# Notes skill",
            skill_type="generic",
        )
        written = execute_skill_tool(
            self.memory_root,
            "coach",
            "write_skill_file",
            {
                "skill_name": "notes-skill",
                "path": "references/notes.md",
                "content": "# My notes\n\nHello voice.",
            },
        )
        self.assertIn("Wrote", written.output)
        self.assertNotIn("Error", written.output)

        read_back = execute_skill_tool(
            self.memory_root,
            "coach",
            "read_skill_file",
            {
                "skill_name": "notes-skill",
                "path": "references/notes.md",
            },
        )
        self.assertIn("Hello voice.", read_back.output)

        file_path = skill.directory / "references" / "notes.md"
        self.assertTrue(file_path.is_file())

    def test_read_skill_file_by_name_without_active_skill(self) -> None:
        result = execute_skill_tool(
            self.memory_root,
            "coach",
            "read_skill_file",
            {
                "skill_name": "equipment-setup",
                "path": "references/details.md",
            },
        )
        self.assertIn("Extra rig details", result.output)

    def test_read_skill_file_path_safety_with_skill_name(self) -> None:
        bad = execute_skill_tool(
            self.memory_root,
            "coach",
            "read_skill_file",
            {
                "skill_name": "equipment-setup",
                "path": "references/../SKILL.md",
            },
        )
        self.assertIn("Error", bad.output)

    def test_write_skill_file_path_safety(self) -> None:
        skill = create_skill(
            "path-test",
            "Path test.",
            "# Path test",
            skill_type="generic",
        )
        bad = execute_skill_tool(
            self.memory_root,
            "coach",
            "write_skill_file",
            {
                "skill_name": "path-test",
                "path": "references/../SKILL.md",
                "content": "nope",
            },
        )
        self.assertIn("Error", bad.output)

    def test_cannot_write_builtin_skill_file(self) -> None:
        result = execute_skill_tool(
            self.memory_root,
            "coach",
            "write_skill_file",
            {
                "skill_name": "edit-personality",
                "path": "references/notes.md",
                "content": "nope",
            },
        )
        self.assertIn("Error", result.output)

    def test_write_session_yaml_validates(self) -> None:
        skill = create_skill(
            "pulse-write",
            "Pulse write test.",
            "# Pulse",
            skill_type="pulse",
        )
        bad = execute_skill_tool(
            self.memory_root,
            "coach",
            "write_skill_file",
            {
                "skill_name": "pulse-write",
                "path": "references/session.yaml",
                "content": "name: pulse-write\nrules: not-a-list\n",
            },
        )
        self.assertIn("Error", bad.output)
        self.assertIn("invalid session.yaml", bad.output)

    def test_update_pulse_config_merges_params(self) -> None:
        skill = create_skill(
            "tune-pulse",
            "Tune me.",
            "# Tune",
            skill_type="pulse",
        )
        result = execute_skill_tool(
            self.memory_root,
            "coach",
            "update_pulse_config",
            {
                "skill_name": "tune-pulse",
                "params": {
                    "camera_switch_interval_s": 300,
                    "cameras": [
                        {"id": 1, "label": "wide"},
                        {"id": 2, "label": "close"},
                    ],
                    "conversation_min_silence_s": 30,
                },
            },
        )
        self.assertIn("Updated pulse config", result.output)
        self.assertNotIn("Error", result.output)

        config = load_session_config(skill.directory, skill_name=skill.name)
        self.assertEqual(config.init_set["switch_interval_s"], 300.0)
        self.assertEqual(config.pulse.conversation_check_s, 30.0)
        self.assertEqual(len(config.cameras), 2)

    def test_update_pulse_config_preserves_custom_rules(self) -> None:
        skill = create_skill(
            "custom-pulse",
            "Custom rules.",
            "# Custom",
            skill_type="pulse",
        )
        session_path = skill.directory / "references" / "session.yaml"
        raw = yaml.safe_load(session_path.read_text(encoding="utf-8"))
        raw["init"]["set"]["custom_flag"] = True
        raw["rules"].append(
            {
                "id": "custom-rule",
                "when": "phase == live",
                "set": {"custom_flag": False},
                "cue": "Custom cue.",
                "priority": "conversational",
            }
        )
        session_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

        execute_skill_tool(
            self.memory_root,
            "coach",
            "update_pulse_config",
            {
                "skill_name": "custom-pulse",
                "params": {"min_speak_interval_s": 90},
            },
        )

        merged = yaml.safe_load(session_path.read_text(encoding="utf-8"))
        self.assertTrue(merged["init"]["set"]["custom_flag"])
        rule_ids = [rule["id"] for rule in merged["rules"]]
        self.assertIn("camera-switch", rule_ids)
        self.assertIn("custom-rule", rule_ids)
        self.assertEqual(merged["pulse"]["min_speak_interval_s"], 90)

    def test_update_pulse_config_rejects_unknown_param(self) -> None:
        skill = create_skill(
            "reject-pulse",
            "Reject unknown.",
            "# Reject",
            skill_type="pulse",
        )
        result = execute_skill_tool(
            self.memory_root,
            "coach",
            "update_pulse_config",
            {
                "skill_name": "reject-pulse",
                "params": {"foo": 1},
            },
        )
        self.assertIn("Error", result.output)
        self.assertIn("Unknown pulse config param", result.output)

    def test_update_pulse_config_rejects_invalid_value(self) -> None:
        skill = create_skill(
            "invalid-pulse",
            "Invalid value.",
            "# Invalid",
            skill_type="pulse",
        )
        result = execute_skill_tool(
            self.memory_root,
            "coach",
            "update_pulse_config",
            {
                "skill_name": "invalid-pulse",
                "params": {"tick_interval_s": -5},
            },
        )
        self.assertIn("Error", result.output)

    def test_update_pulse_config_rejects_checklist_skill(self) -> None:
        result = execute_skill_tool(
            self.memory_root,
            "coach",
            "update_pulse_config",
            {
                "skill_name": "equipment-setup",
                "params": {"min_speak_interval_s": 30},
            },
        )
        self.assertIn("Error", result.output)
        self.assertIn("not a pulse skill", result.output)

    def test_update_pulse_config_keep_them_talking(self) -> None:
        skill = create_skill(
            "filming-pulse",
            "Filming mode.",
            "# Filming",
            skill_type="pulse",
        )
        result = execute_skill_tool(
            self.memory_root,
            "coach",
            "update_pulse_config",
            {
                "skill_name": "filming-pulse",
                "params": {"keep_them_talking": True},
            },
        )
        self.assertIn("Updated pulse config", result.output)
        self.assertNotIn("Error", result.output)

        config = load_session_config(skill.directory, skill_name=skill.name)
        self.assertTrue(config.pulse.silence_gated_only)


class PulseConfigMergeUnitTests(unittest.TestCase):
    def test_merge_pulse_params_maps_keys(self) -> None:
        raw = {
            "name": "test",
            "init": {"set": {"phase": "live"}},
            "rules": [],
            "schedule": [],
        }
        changed = merge_pulse_params(
            raw,
            {
                "camera_switch_interval_s": 120,
                "conversation_min_silence_s": 45,
            },
        )
        self.assertEqual(changed, ["camera_switch_interval_s", "conversation_min_silence_s"])
        self.assertEqual(raw["init"]["set"]["switch_interval_s"], 120)
        self.assertEqual(raw["pulse"]["conversation_check_s"], 45)

    def test_merge_keep_them_talking_maps_to_silence_gated_only(self) -> None:
        raw = {
            "name": "test",
            "init": {"set": {"phase": "live"}},
            "rules": [],
            "schedule": [],
        }
        changed = merge_pulse_params(raw, {"keep_them_talking": True})
        self.assertEqual(changed, ["keep_them_talking"])
        self.assertTrue(raw["pulse"]["silence_gated_only"])

    def test_merge_keep_them_talking_rejects_non_boolean(self) -> None:
        raw = {"name": "test", "rules": [], "schedule": []}
        with self.assertRaises(Exception) as ctx:
            merge_pulse_params(raw, {"keep_them_talking": 1})
        self.assertIn("keep_them_talking must be a boolean", str(ctx.exception))

    def test_apply_pulse_config_writes_valid_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / "my-pulse"
            refs = skill_dir / "references"
            refs.mkdir(parents=True)
            (refs / "session.yaml").write_text(
                "name: my-pulse\n"
                "init:\n  set:\n    phase: live\n"
                "rules: []\nschedule: []\n",
                encoding="utf-8",
            )
            config = apply_pulse_config(skill_dir, {"tick_interval_s": 8}, skill_name="my-pulse")
            self.assertEqual(config.pulse.tick_interval_s, 8.0)
            reloaded = load_session_config(skill_dir, skill_name="my-pulse")
            self.assertEqual(reloaded.pulse.tick_interval_s, 8.0)


if __name__ == "__main__":
    unittest.main()
