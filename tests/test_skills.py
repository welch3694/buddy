"""Tests for buddy_tools.skills — loader, state, tools, and instruction injection."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from buddy_tools import personality as personality_module
from buddy_tools import voices as voices_module
from buddy_tools.bootstrap import set_memory_root
from buddy_tools.personality import create_personality, get_personality, set_personalities_dir
from buddy_tools.personality_session import apply_personality_switch
from buddy_tools.registry import ALL_TOOL_DEFINITIONS, build_tool_instructions, execute_tool
from buddy_tools.skills import (
    discover_skills,
    execute_skill_tool,
    load_skill_definition,
    load_skill_state,
    save_skill_state,
    SkillState,
)
from buddy_tools.voices import set_voices_dir
from speech_to_speech.LLM.chat import Chat
from speech_to_speech.api.openai_realtime.runtime_config import RuntimeConfig

SAMPLE_CHECKLIST_SKILL = """\
---
name: equipment-setup
description: Guide the user through pre-session rig checks. Use when they say set up or prep the rig.
metadata:
  buddy:
    type: checklist
---

# Equipment setup

Walk the user through one step at a time.

## Steps

### mic
Is your microphone connected and selected as the input device?

### headphones
Put on headphones to avoid feedback.
"""


class SkillLoaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.skills_dir = self.root / "equipment-setup"
        self.skills_dir.mkdir(parents=True)
        (self.skills_dir / "SKILL.md").write_text(SAMPLE_CHECKLIST_SKILL, encoding="utf-8")

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_load_skill_definition_parses_frontmatter_and_steps(self) -> None:
        skill = load_skill_definition(self.skills_dir)
        self.assertEqual(skill.name, "equipment-setup")
        self.assertIn("pre-session rig checks", skill.description)
        self.assertEqual(skill.skill_type, "checklist")
        self.assertEqual(len(skill.steps), 2)
        self.assertEqual(skill.steps[0].step_id, "mic")
        self.assertIn("microphone", skill.steps[0].prompt)

    def test_rejects_name_directory_mismatch(self) -> None:
        bad_dir = self.root / "wrong-name"
        bad_dir.mkdir()
        (bad_dir / "SKILL.md").write_text(SAMPLE_CHECKLIST_SKILL, encoding="utf-8")
        with self.assertRaises(ValueError) as ctx:
            load_skill_definition(bad_dir)
        self.assertIn("does not match directory", str(ctx.exception))


class SkillToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_personalities_dir = personality_module.get_personalities_dir()
        self._original_voices_dir = voices_module.get_voices_dir()
        self._original_memory_root = None
        from buddy_tools.bootstrap import get_memory_root

        self._original_memory_root = get_memory_root()

        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.personalities_root = self.root / "personalities"
        self.voices_root = self.root / "voices"
        self.memory_root = self.root / "memory"
        self.personalities_root.mkdir()
        self.voices_root.mkdir()
        self.memory_root.mkdir()

        set_personalities_dir(self.personalities_root)
        set_voices_dir(self.voices_root)
        set_memory_root(self.memory_root)

        self._write_voice("cliff")
        self._write_voice("narrator")
        create_personality("buddy", "Buddy", "You are Buddy.", voice_id="cliff")
        create_personality("coach", "Coach", "You are Coach.", voice_id="narrator")
        self._write_checklist_skill("coach", "equipment-setup")

    def tearDown(self) -> None:
        set_personalities_dir(self._original_personalities_dir)
        set_voices_dir(self._original_voices_dir)
        if self._original_memory_root is not None:
            set_memory_root(self._original_memory_root)
        self._tmpdir.cleanup()

    def _write_voice(self, voice_id: str) -> None:
        voice_dir = self.voices_root / voice_id
        voice_dir.mkdir(parents=True, exist_ok=True)
        (voice_dir / "audio.wav").write_bytes(b"RIFF")
        (voice_dir / "ref_text.txt").write_text(f"{voice_id} transcript", encoding="utf-8")

    def _write_checklist_skill(self, personality_id: str, skill_name: str) -> None:
        skill_dir = self.personalities_root / personality_id / "skills" / skill_name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(SAMPLE_CHECKLIST_SKILL, encoding="utf-8")
        refs = skill_dir / "references"
        refs.mkdir()
        (refs / "details.md").write_text("Extra rig details here.", encoding="utf-8")

    def test_registry_includes_skill_tools(self) -> None:
        names = {tool.name for tool in ALL_TOOL_DEFINITIONS}
        self.assertIn("list_skills", names)
        self.assertIn("start_skill", names)
        self.assertIn("advance_skill", names)
        self.assertIn("cancel_skill", names)

    def test_list_skills_returns_metadata_only(self) -> None:
        from buddy_tools.personality import set_active_personality

        set_active_personality("coach")
        result = execute_skill_tool(self.memory_root, "coach", "list_skills", {})
        payload = json.loads(result.output)
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["name"], "equipment-setup")
        self.assertIn("description", payload[0])
        self.assertNotIn("body", payload[0])

    def test_start_advance_pause_cancel_flow(self) -> None:
        from buddy_tools.personality import set_active_personality

        set_active_personality("coach")

        started = execute_skill_tool(
            self.memory_root, "coach", "start_skill", {"name": "equipment-setup"}
        )
        self.assertIn("Started", started.output)
        self.assertTrue(started.refresh_instructions)
        self.assertTrue(started.include_full_skill_body)

        state = load_skill_state(self.memory_root, "coach")
        assert state is not None
        self.assertEqual(state.skill_name, "equipment-setup")
        self.assertEqual(state.status, "in_progress")
        self.assertEqual(state.step_index, 0)

        advanced = execute_skill_tool(self.memory_root, "coach", "advance_skill", {})
        self.assertIn("step 2 of 2", advanced.output)
        state = load_skill_state(self.memory_root, "coach")
        assert state is not None
        self.assertEqual(state.step_index, 1)

        paused = execute_skill_tool(self.memory_root, "coach", "pause_skill", {})
        self.assertIn("Paused", paused.output)
        state = load_skill_state(self.memory_root, "coach")
        assert state is not None
        self.assertEqual(state.status, "paused")

        resumed = execute_skill_tool(
            self.memory_root, "coach", "start_skill", {"name": "equipment-setup"}
        )
        self.assertIn("Resumed", resumed.output)
        state = load_skill_state(self.memory_root, "coach")
        assert state is not None
        self.assertEqual(state.status, "in_progress")
        self.assertEqual(state.step_index, 1)

        completed = execute_skill_tool(self.memory_root, "coach", "advance_skill", {})
        self.assertIn("Completed", completed.output)
        self.assertIsNone(load_skill_state(self.memory_root, "coach"))

    def test_cancel_skill_clears_state(self) -> None:
        from buddy_tools.personality import set_active_personality

        set_active_personality("coach")
        execute_skill_tool(self.memory_root, "coach", "start_skill", {"name": "equipment-setup"})
        cancelled = execute_skill_tool(self.memory_root, "coach", "cancel_skill", {})
        self.assertIn("Cancelled", cancelled.output)
        self.assertIsNone(load_skill_state(self.memory_root, "coach"))

    def test_read_skill_file_path_safety(self) -> None:
        from buddy_tools.personality import set_active_personality

        set_active_personality("coach")
        execute_skill_tool(self.memory_root, "coach", "start_skill", {"name": "equipment-setup"})

        ok = execute_skill_tool(
            self.memory_root,
            "coach",
            "read_skill_file",
            {"path": "references/details.md"},
        )
        self.assertIn("Extra rig details", ok.output)

        bad = execute_skill_tool(
            self.memory_root,
            "coach",
            "read_skill_file",
            {"path": "references/../SKILL.md"},
        )
        self.assertIn("Error", bad.output)

    def test_execute_tool_dispatches_skill_tools(self) -> None:
        from buddy_tools.personality import set_active_personality

        set_active_personality("coach")
        result = execute_tool(
            self.memory_root,
            "list_skills",
            "{}",
            persona_namespace="coach",
        )
        payload = json.loads(result.output)
        self.assertEqual(payload[0]["name"], "equipment-setup")

    def test_build_tool_instructions_includes_active_skill_context(self) -> None:
        save_skill_state(
            self.memory_root,
            "coach",
            SkillState(
                skill_name="equipment-setup",
                status="in_progress",
                step_index=0,
                skill_type="checklist",
            ),
        )
        profile = get_personality("coach")
        text = build_tool_instructions(
            "Coach prompt.",
            "(no memory saved yet)",
            memory_root=self.memory_root,
            persona_namespace="coach",
            personality_id="coach",
        )
        self.assertIn("Active skill context", text)
        self.assertIn("equipment-setup", text)
        self.assertIn("step 1 of 2", text)
        self.assertIn("microphone", text)

    def test_skill_state_persists_across_personality_switch(self) -> None:
        from buddy_tools.personality import set_active_personality

        set_active_personality("coach")
        execute_skill_tool(self.memory_root, "coach", "start_skill", {"name": "equipment-setup"})
        execute_skill_tool(self.memory_root, "coach", "advance_skill", {})

        coach_state = load_skill_state(self.memory_root, "coach")
        assert coach_state is not None
        self.assertEqual(coach_state.step_index, 1)

        runtime_config = RuntimeConfig()
        chat = Chat(10)
        apply_personality_switch(
            "buddy",
            runtime_config=runtime_config,
            chat=chat,
            memory_root=self.memory_root,
        )
        self.assertIsNone(load_skill_state(self.memory_root, "buddy"))
        self.assertNotIn("equipment-setup", runtime_config.session.instructions)

        apply_personality_switch(
            "coach",
            runtime_config=runtime_config,
            chat=chat,
            memory_root=self.memory_root,
        )
        restored = load_skill_state(self.memory_root, "coach")
        assert restored is not None
        self.assertEqual(restored.step_index, 1)
        self.assertIn("equipment-setup", runtime_config.session.instructions)

    def test_discover_skills_skips_invalid(self) -> None:
        bad_dir = self.personalities_root / "coach" / "skills" / "bad-skill"
        bad_dir.mkdir(parents=True)
        (bad_dir / "SKILL.md").write_text("No frontmatter here.", encoding="utf-8")

        profile = get_personality("coach")
        skills = discover_skills(profile)
        names = [s.name for s in skills]
        self.assertIn("equipment-setup", names)
        self.assertNotIn("bad-skill", names)


if __name__ == "__main__":
    unittest.main()
