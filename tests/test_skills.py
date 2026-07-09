"""Tests for buddy_tools.skills — loader, state, tools, and instruction injection."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from buddy_tools import personality as personality_module
import buddy_tools.voice.voices as voices_module
from buddy_tools.infra.bootstrap import set_memory_root
from buddy_tools.infra.data_dir import get_built_in_skills_dir, get_user_skills_dir, reset_data_dir_config
from buddy_tools.personality import create_personality, get_personality, set_active_personality, set_personalities_dir
from buddy_tools.personality.session import apply_personality_switch
from buddy_tools.core.registry import ALL_TOOL_DEFINITIONS, build_tool_instructions, execute_tool
from buddy_tools.skills import (
    discover_skills,
    execute_skill_tool,
    get_skill_definition,
    load_skill_definition,
    load_skill_state,
    save_skill_state,
    SkillState,
)
from buddy_tools.voice.voices import set_voices_dir
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

    def test_parses_numbered_step_headings(self) -> None:
        content = """\
---
name: director-flow
description: Guide a director flow.
metadata:
  buddy:
    type: checklist
---

# Director Flow

## Steps

### 1. Preparation & Setup
Confirm the camera is positioned correctly and the user is ready.

### 2. Introduction Phase
Welcome the audience and set the tone.

### 3. Core Phase
Run the main segment with timer cues.
"""
        skill_dir = self.root / "director-flow"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
        skill = load_skill_definition(skill_dir)
        self.assertEqual(len(skill.steps), 3)
        self.assertEqual(skill.steps[0].step_id, "preparation-setup")
        self.assertIn("camera", skill.steps[0].prompt.lower())
        self.assertEqual(skill.steps[1].step_id, "introduction-phase")

    def test_slug_step_headings_still_work(self) -> None:
        skill = load_skill_definition(self.skills_dir)
        self.assertEqual(skill.steps[0].step_id, "mic")
        self.assertEqual(skill.steps[1].step_id, "headphones")


class SkillToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_personalities_dir = personality_module.get_personalities_dir()
        self._original_voices_dir = voices_module.get_voices_dir()
        self._original_memory_root = None
        from buddy_tools.infra.bootstrap import get_memory_root

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

        self._write_voice("cliff")
        self._write_voice("narrator")
        create_personality("buddy", "Buddy", "You are Buddy.", voice_id="cliff")
        create_personality("coach", "Coach", "You are Coach.", voice_id="narrator")
        self._write_checklist_skill("coach", "equipment-setup")

    def tearDown(self) -> None:
        reset_data_dir_config()
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
        self.assertIn("create_skill", names)
        self.assertIn("update_skill", names)
        self.assertIn("delete_skill", names)
        self.assertIn("write_skill_file", names)
        self.assertIn("update_pulse_config", names)

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


class GlobalBuiltinSkillTests(unittest.TestCase):
    BUILTIN_SKILL = """\
---
name: edit-personality
description: Safely update prompt.md with persona-only content.
metadata:
  buddy:
    type: checklist
---

# Edit personality

## Steps

### confirm-target
Which personality are we editing?

### confirm-changes
What should change?
"""

    PERSONA_OVERRIDE_SKILL = """\
---
name: edit-personality
description: Persona-specific override for edit-personality.
metadata:
  buddy:
    type: checklist
---

# Persona edit personality

## Steps

### step-one
Persona override step.
"""

    def setUp(self) -> None:
        self._original_personalities_dir = personality_module.get_personalities_dir()
        self._original_voices_dir = voices_module.get_voices_dir()
        self._original_memory_root = None
        from buddy_tools.infra.bootstrap import get_memory_root

        self._original_memory_root = get_memory_root()

        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.repo_root = self.root / "repo"
        self.personalities_root = self.root / "data" / "personalities"
        self.voices_root = self.root / "voices"
        self.memory_root = self.root / "data" / "memory"
        self.builtin_skills_root = self.repo_root / "skills"

        for path in (self.personalities_root, self.voices_root, self.memory_root):
            path.mkdir(parents=True)
        self.builtin_skills_root.mkdir(parents=True)

        reset_data_dir_config(repo_root=self.repo_root, data_dir=self.root / "data")
        set_personalities_dir(self.personalities_root)
        set_voices_dir(self.voices_root)
        set_memory_root(self.memory_root)

        self._write_voice("cliff")
        create_personality("buddy", "Buddy", "You are Buddy.", voice_id="cliff")
        self._write_builtin_skill("edit-personality", self.BUILTIN_SKILL)

    def tearDown(self) -> None:
        reset_data_dir_config()
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

    def _write_builtin_skill(self, skill_name: str, content: str) -> None:
        skill_dir = self.builtin_skills_root / skill_name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")

    def _write_persona_skill(self, personality_id: str, skill_name: str, content: str) -> None:
        skill_dir = self.personalities_root / personality_id / "skills" / skill_name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")

    def test_builtin_skills_dir_points_at_repo_skills(self) -> None:
        self.assertEqual(get_built_in_skills_dir(), self.repo_root / "skills")

    def test_discover_builtin_without_persona_skills_dir(self) -> None:
        profile = get_personality("buddy")
        self.assertFalse((profile.directory / "skills").exists())

        skills = discover_skills(profile)
        self.assertEqual(len(skills), 1)
        self.assertEqual(skills[0].name, "edit-personality")
        self.assertEqual(skills[0].source, "builtin")

    def test_list_skills_tags_source(self) -> None:
        set_active_personality("buddy")
        result = execute_skill_tool(self.memory_root, "buddy", "list_skills", {})
        payload = json.loads(result.output)
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["source"], "builtin")

    def test_start_builtin_skill_without_persona_copy(self) -> None:
        set_active_personality("buddy")
        started = execute_skill_tool(
            self.memory_root,
            "buddy",
            "start_skill",
            {"name": "edit-personality"},
        )
        self.assertIn("Started", started.output)
        self.assertIn("confirm-target", started.output)

    def test_get_skill_definition_resolves_builtin(self) -> None:
        profile = get_personality("buddy")
        skill = get_skill_definition(profile, "edit-personality")
        self.assertEqual(skill.source, "builtin")
        self.assertEqual(skill.name, "edit-personality")

    def test_persona_skill_overrides_builtin_on_collision(self) -> None:
        self._write_persona_skill("buddy", "edit-personality", self.PERSONA_OVERRIDE_SKILL)

        profile = get_personality("buddy")
        skills = discover_skills(profile)
        self.assertEqual(len(skills), 1)
        self.assertEqual(skills[0].source, "personality")
        self.assertIn("Persona-specific override", skills[0].description)

        skill = get_skill_definition(profile, "edit-personality")
        self.assertEqual(skill.source, "personality")
        self.assertIn("Persona override step", skill.steps[0].prompt)

    def test_repo_edit_personality_skill_is_valid(self) -> None:
        project_root = Path(__file__).resolve().parent.parent
        skill_dir = project_root / "skills" / "edit-personality"
        skill = load_skill_definition(skill_dir, source="builtin")
        self.assertEqual(skill.name, "edit-personality")
        self.assertEqual(skill.skill_type, "checklist")
        self.assertGreaterEqual(len(skill.steps), 2)
        refs = skill_dir / "references" / "prompt-guidelines.md"
        self.assertTrue(refs.is_file())


class RememberSkillTests(unittest.TestCase):
    REMEMBER_SKILL = """\
---
name: remember
description: Save a fact with global vs persona scope. Use when the user says remember that.
metadata:
  buddy:
    type: checklist
---

# Remember

## Steps

### confirm-fact
Restate and confirm.

### choose-scope
Share with everyone or keep it between us?

### save-memory
Use update_memory or append_memory with scope global or persona.

### confirm-saved
Confirm where it was saved.
"""

    def setUp(self) -> None:
        self._original_personalities_dir = personality_module.get_personalities_dir()
        self._original_voices_dir = voices_module.get_voices_dir()
        self._original_memory_root = None
        from buddy_tools.infra.bootstrap import get_memory_root

        self._original_memory_root = get_memory_root()

        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.repo_root = self.root / "repo"
        self.personalities_root = self.root / "data" / "personalities"
        self.voices_root = self.root / "voices"
        self.memory_root = self.root / "data" / "memory"
        self.builtin_skills_root = self.repo_root / "skills"

        for path in (self.personalities_root, self.voices_root, self.memory_root):
            path.mkdir(parents=True)
        self.builtin_skills_root.mkdir(parents=True)

        reset_data_dir_config(repo_root=self.repo_root, data_dir=self.root / "data")
        set_personalities_dir(self.personalities_root)
        set_voices_dir(self.voices_root)
        set_memory_root(self.memory_root)

        self._write_voice("cliff")
        self._write_voice("narrator")
        create_personality("buddy", "Buddy", "You are Buddy.", voice_id="cliff")
        create_personality("coach", "Coach", "You are Coach.", voice_id="narrator")
        self._write_builtin_skill("remember", self.REMEMBER_SKILL)

    def tearDown(self) -> None:
        reset_data_dir_config()
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

    def _write_builtin_skill(self, skill_name: str, content: str) -> None:
        skill_dir = self.builtin_skills_root / skill_name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")

    def test_remember_skill_discoverable_for_all_personalities(self) -> None:
        for personality_id in ("buddy", "coach"):
            profile = get_personality(personality_id)
            skills = discover_skills(profile)
            names = {skill.name: skill.source for skill in skills}
            self.assertIn("remember", names)
            self.assertEqual(names["remember"], "builtin")

    def test_list_skills_includes_remember_as_builtin(self) -> None:
        set_active_personality("buddy")
        result = execute_skill_tool(self.memory_root, "buddy", "list_skills", {})
        payload = json.loads(result.output)
        remember_entries = [entry for entry in payload if entry["name"] == "remember"]
        self.assertEqual(len(remember_entries), 1)
        self.assertEqual(remember_entries[0]["source"], "builtin")

    def test_repo_remember_skill_is_valid_and_covers_scope(self) -> None:
        project_root = Path(__file__).resolve().parent.parent
        skill_dir = project_root / "skills" / "remember"
        skill = load_skill_definition(skill_dir, source="builtin")
        self.assertEqual(skill.name, "remember")
        self.assertEqual(skill.skill_type, "checklist")
        self.assertEqual(len(skill.steps), 4)
        self.assertEqual(skill.steps[0].step_id, "confirm-fact")
        self.assertEqual(skill.steps[1].step_id, "choose-scope")

        body_lower = skill.body.lower()
        self.assertIn("share with everyone", body_lower)
        self.assertIn("between us", body_lower)
        self.assertIn("append_memory", skill.body)
        self.assertIn("update_memory", skill.body)
        self.assertIn("scope: global", body_lower)
        self.assertIn("scope: persona", body_lower)

        description_lower = skill.description.lower()
        self.assertIn("remember", description_lower)
        self.assertIn("start_skill", description_lower)


class SharedUserSkillTests(unittest.TestCase):
    SHARED_SKILL_ALL = """\
---
name: equipment-setup
description: Shared rig setup for all personas.
metadata:
  buddy:
    type: checklist
---

# Equipment setup

## Steps

### mic
Check the microphone.

### headphones
Put on headphones.
"""

    SHARED_SKILL_COACH_ONLY = """\
---
name: coach-warmup
description: Coach-only warmup checklist.
metadata:
  buddy:
    type: checklist
    personalities: [coach]
---

# Coach warmup

## Steps

### stretch
Do a quick stretch.
"""

    SHARED_OVERRIDE_BUILTIN = """\
---
name: edit-personality
description: Shared override for edit-personality.
metadata:
  buddy:
    type: checklist
---

# Shared edit personality

## Steps

### shared-step
Shared override step.
"""

    PERSONA_OVERRIDE_SKILL = """\
---
name: edit-personality
description: Persona-specific override for edit-personality.
metadata:
  buddy:
    type: checklist
---

# Persona edit personality

## Steps

### step-one
Persona override step.
"""

    BUILTIN_SKILL = GlobalBuiltinSkillTests.BUILTIN_SKILL

    def setUp(self) -> None:
        self._original_personalities_dir = personality_module.get_personalities_dir()
        self._original_voices_dir = voices_module.get_voices_dir()
        self._original_memory_root = None
        from buddy_tools.infra.bootstrap import get_memory_root

        self._original_memory_root = get_memory_root()

        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.repo_root = self.root / "repo"
        self.data_dir = self.root / "data"
        self.personalities_root = self.data_dir / "personalities"
        self.shared_skills_root = self.data_dir / "skills"
        self.voices_root = self.root / "voices"
        self.memory_root = self.data_dir / "memory"
        self.builtin_skills_root = self.repo_root / "skills"

        for path in (
            self.personalities_root,
            self.shared_skills_root,
            self.voices_root,
            self.memory_root,
        ):
            path.mkdir(parents=True)
        self.builtin_skills_root.mkdir(parents=True)

        reset_data_dir_config(repo_root=self.repo_root, data_dir=self.data_dir)
        set_personalities_dir(self.personalities_root)
        set_voices_dir(self.voices_root)
        set_memory_root(self.memory_root)

        self._write_voice("cliff")
        self._write_voice("narrator")
        create_personality("buddy", "Buddy", "You are Buddy.", voice_id="cliff")
        create_personality("coach", "Coach", "You are Coach.", voice_id="narrator")
        self._write_builtin_skill("edit-personality", self.BUILTIN_SKILL)

    def tearDown(self) -> None:
        reset_data_dir_config()
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

    def _write_builtin_skill(self, skill_name: str, content: str) -> None:
        skill_dir = self.builtin_skills_root / skill_name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")

    def _write_shared_skill(self, skill_name: str, content: str) -> None:
        skill_dir = self.shared_skills_root / skill_name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")

    def _write_persona_skill(self, personality_id: str, skill_name: str, content: str) -> None:
        skill_dir = self.personalities_root / personality_id / "skills" / skill_name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")

    def test_user_skills_dir_points_at_data_dir_skills(self) -> None:
        self.assertEqual(get_user_skills_dir(), self.data_dir / "skills")

    def test_discover_shared_skill_visible_to_all(self) -> None:
        self._write_shared_skill("equipment-setup", self.SHARED_SKILL_ALL)

        buddy_skills = discover_skills(get_personality("buddy"))
        coach_skills = discover_skills(get_personality("coach"))

        buddy_names = {skill.name: skill.source for skill in buddy_skills}
        coach_names = {skill.name: skill.source for skill in coach_skills}
        self.assertEqual(buddy_names["equipment-setup"], "shared")
        self.assertEqual(coach_names["equipment-setup"], "shared")

    def test_shared_skill_scoped_to_subset(self) -> None:
        self._write_shared_skill("coach-warmup", self.SHARED_SKILL_COACH_ONLY)

        buddy_skills = discover_skills(get_personality("buddy"))
        coach_skills = discover_skills(get_personality("coach"))

        buddy_names = [skill.name for skill in buddy_skills]
        coach_names = [skill.name for skill in coach_skills]
        self.assertNotIn("coach-warmup", buddy_names)
        self.assertIn("coach-warmup", coach_names)

    def test_shared_overrides_builtin_on_collision(self) -> None:
        self._write_shared_skill("edit-personality", self.SHARED_OVERRIDE_BUILTIN)

        profile = get_personality("buddy")
        skills = discover_skills(profile)
        self.assertEqual(len(skills), 1)
        self.assertEqual(skills[0].source, "shared")
        self.assertIn("Shared override", skills[0].description)

        skill = get_skill_definition(profile, "edit-personality")
        self.assertEqual(skill.source, "shared")
        self.assertIn("Shared override step", skill.steps[0].prompt)

    def test_persona_overrides_shared_overrides_builtin(self) -> None:
        self._write_shared_skill("edit-personality", self.SHARED_OVERRIDE_BUILTIN)
        self._write_persona_skill("buddy", "edit-personality", self.PERSONA_OVERRIDE_SKILL)

        profile = get_personality("buddy")
        skills = discover_skills(profile)
        self.assertEqual(len(skills), 1)
        self.assertEqual(skills[0].source, "personality")

        skill = get_skill_definition(profile, "edit-personality")
        self.assertEqual(skill.source, "personality")
        self.assertIn("Persona override step", skill.steps[0].prompt)

    def test_get_skill_definition_resolves_shared(self) -> None:
        self._write_shared_skill("equipment-setup", self.SHARED_SKILL_ALL)

        profile = get_personality("coach")
        skill = get_skill_definition(profile, "equipment-setup")
        self.assertEqual(skill.source, "shared")
        self.assertEqual(skill.name, "equipment-setup")

    def test_list_skills_tags_shared_source_and_scope(self) -> None:
        self._write_shared_skill("equipment-setup", self.SHARED_SKILL_ALL)
        self._write_shared_skill("coach-warmup", self.SHARED_SKILL_COACH_ONLY)

        set_active_personality("coach")
        result = execute_skill_tool(self.memory_root, "coach", "list_skills", {})
        payload = json.loads(result.output)
        by_name = {entry["name"]: entry for entry in payload}

        self.assertEqual(by_name["equipment-setup"]["source"], "shared")
        self.assertEqual(by_name["equipment-setup"]["scope"], "all")
        self.assertEqual(by_name["coach-warmup"]["source"], "shared")
        self.assertEqual(by_name["coach-warmup"]["scope"], ["coach"])
        self.assertEqual(by_name["edit-personality"]["source"], "builtin")
        self.assertNotIn("scope", by_name["edit-personality"])

    def test_builtin_unaffected_by_shared_layer(self) -> None:
        profile = get_personality("buddy")
        skills = discover_skills(profile)
        self.assertEqual(len(skills), 1)
        self.assertEqual(skills[0].name, "edit-personality")
        self.assertEqual(skills[0].source, "builtin")

    def test_shared_skill_state_remains_per_persona(self) -> None:
        self._write_shared_skill("equipment-setup", self.SHARED_SKILL_ALL)

        set_active_personality("coach")
        execute_skill_tool(
            self.memory_root,
            "coach",
            "start_skill",
            {"name": "equipment-setup"},
        )
        execute_skill_tool(
            self.memory_root,
            "coach",
            "advance_skill",
            {"skip": False},
        )

        set_active_personality("buddy")
        execute_skill_tool(
            self.memory_root,
            "buddy",
            "start_skill",
            {"name": "equipment-setup"},
        )

        coach_state = load_skill_state(self.memory_root, "coach")
        buddy_state = load_skill_state(self.memory_root, "buddy")
        self.assertIsNotNone(coach_state)
        self.assertIsNotNone(buddy_state)
        self.assertEqual(coach_state.step_index, 1)
        self.assertEqual(buddy_state.step_index, 0)


class CreateSkillToolTests(unittest.TestCase):
    GENERIC_BODY = """\
# Director flow

Walk the user through directing a scene.
"""

    CHECKLIST_BODY = """\
# Warmup

## Steps

### stretch
Do a quick stretch.

### breathe
Take three deep breaths.
"""

    NUMBERED_CHECKLIST_BODY = """\
# Director Flow

## Steps

### 1. Preparation & Setup
Confirm the camera is positioned correctly.

### 2. Introduction Phase
Welcome the audience.
"""

    BUILTIN_SKILL = GlobalBuiltinSkillTests.BUILTIN_SKILL

    def setUp(self) -> None:
        self._original_personalities_dir = personality_module.get_personalities_dir()
        self._original_voices_dir = voices_module.get_voices_dir()
        self._original_memory_root = None
        from buddy_tools.infra.bootstrap import get_memory_root

        self._original_memory_root = get_memory_root()

        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.repo_root = self.root / "repo"
        self.data_dir = self.root / "data"
        self.personalities_root = self.data_dir / "personalities"
        self.shared_skills_root = self.data_dir / "skills"
        self.voices_root = self.root / "voices"
        self.memory_root = self.data_dir / "memory"
        self.builtin_skills_root = self.repo_root / "skills"

        for path in (
            self.personalities_root,
            self.shared_skills_root,
            self.voices_root,
            self.memory_root,
        ):
            path.mkdir(parents=True)
        self.builtin_skills_root.mkdir(parents=True)

        reset_data_dir_config(repo_root=self.repo_root, data_dir=self.data_dir)
        set_personalities_dir(self.personalities_root)
        set_voices_dir(self.voices_root)
        set_memory_root(self.memory_root)

        self._write_voice("cliff")
        self._write_voice("narrator")
        create_personality("buddy", "Buddy", "You are Buddy.", voice_id="cliff")
        create_personality("coach", "Coach", "You are Coach.", voice_id="narrator")
        self._write_builtin_skill("edit-personality", self.BUILTIN_SKILL)

    def tearDown(self) -> None:
        reset_data_dir_config()
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

    def _write_builtin_skill(self, skill_name: str, content: str) -> None:
        skill_dir = self.builtin_skills_root / skill_name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")

    def test_create_skill_accepts_numbered_step_headings(self) -> None:
        set_active_personality("coach")
        result = execute_skill_tool(
            self.memory_root,
            "coach",
            "create_skill",
            {
                "name": "director-flow",
                "description": "Guide scene direction.",
                "body": self.NUMBERED_CHECKLIST_BODY,
                "skill_type": "checklist",
            },
        )
        self.assertIn("Created skill", result.output)
        self.assertNotIn("Error", result.output)

        skill_path = self.personalities_root / "coach" / "skills" / "director-flow" / "SKILL.md"
        skill = load_skill_definition(skill_path.parent, source="personality")
        self.assertEqual(len(skill.steps), 2)
        self.assertEqual(skill.steps[0].step_id, "preparation-setup")

    def test_create_skill_infers_checklist_from_steps_section(self) -> None:
        set_active_personality("coach")
        result = execute_skill_tool(
            self.memory_root,
            "coach",
            "create_skill",
            {
                "name": "inferred-checklist",
                "description": "Checklist inferred from body.",
                "body": self.NUMBERED_CHECKLIST_BODY,
            },
        )
        self.assertIn("Created skill", result.output)

        skill_path = self.personalities_root / "coach" / "skills" / "inferred-checklist" / "SKILL.md"
        skill = load_skill_definition(skill_path.parent, source="personality")
        self.assertEqual(skill.skill_type, "checklist")
        self.assertEqual(len(skill.steps), 2)

    def test_create_skill_defaults_to_persona_path(self) -> None:
        set_active_personality("coach")
        result = execute_skill_tool(
            self.memory_root,
            "coach",
            "create_skill",
            {
                "name": "director-flow",
                "description": "Guide scene direction.",
                "body": self.GENERIC_BODY,
            },
        )
        self.assertIn("Created skill", result.output)
        self.assertIn("source: personality", result.output)

        skill_path = self.personalities_root / "coach" / "skills" / "director-flow" / "SKILL.md"
        self.assertTrue(skill_path.is_file())
        skill = load_skill_definition(skill_path.parent, source="personality")
        self.assertEqual(skill.name, "director-flow")
        self.assertEqual(skill.source, "personality")

    def test_create_skill_shared_scope(self) -> None:
        set_active_personality("coach")
        result = execute_skill_tool(
            self.memory_root,
            "coach",
            "create_skill",
            {
                "name": "shared-warmup",
                "description": "Warmup for all personas.",
                "body": self.CHECKLIST_BODY,
                "scope": "shared",
                "skill_type": "checklist",
            },
        )
        self.assertIn("source: shared", result.output)

        skill_path = self.shared_skills_root / "shared-warmup" / "SKILL.md"
        self.assertTrue(skill_path.is_file())
        skill = load_skill_definition(skill_path.parent, source="shared")
        self.assertEqual(skill.skill_type, "checklist")
        self.assertEqual(len(skill.steps), 2)

        buddy_skills = discover_skills(get_personality("buddy"))
        names = {s.name: s.source for s in buddy_skills}
        self.assertEqual(names["shared-warmup"], "shared")

    def test_create_skill_validation_errors(self) -> None:
        set_active_personality("coach")

        bad_name = execute_skill_tool(
            self.memory_root,
            "coach",
            "create_skill",
            {
                "name": "!!!",
                "description": "Test",
                "body": self.GENERIC_BODY,
            },
        )
        self.assertIn("Error", bad_name.output)

        missing_desc = execute_skill_tool(
            self.memory_root,
            "coach",
            "create_skill",
            {"name": "valid-name", "description": "", "body": self.GENERIC_BODY},
        )
        self.assertIn("Error", missing_desc.output)

        checklist_no_steps = execute_skill_tool(
            self.memory_root,
            "coach",
            "create_skill",
            {
                "name": "empty-checklist",
                "description": "Missing steps.",
                "body": "# No steps here",
                "skill_type": "checklist",
            },
        )
        self.assertIn("Error", checklist_no_steps.output)

    def test_create_skill_discoverable_via_list_skills(self) -> None:
        set_active_personality("coach")
        execute_skill_tool(
            self.memory_root,
            "coach",
            "create_skill",
            {
                "name": "my-workflow",
                "description": "A coach workflow.",
                "body": self.GENERIC_BODY,
            },
        )

        result = execute_skill_tool(self.memory_root, "coach", "list_skills", {})
        payload = json.loads(result.output)
        by_name = {entry["name"]: entry for entry in payload}
        self.assertEqual(by_name["my-workflow"]["source"], "personality")

    def test_persona_skill_overrides_shared_and_builtin(self) -> None:
        set_active_personality("buddy")
        execute_skill_tool(
            self.memory_root,
            "buddy",
            "create_skill",
            {
                "name": "edit-personality",
                "description": "Persona-authored override.",
                "body": """\
# Override

## Steps

### only-step
Persona-authored step.
""",
                "skill_type": "checklist",
            },
        )

        profile = get_personality("buddy")
        skill = get_skill_definition(profile, "edit-personality")
        self.assertEqual(skill.source, "personality")
        self.assertIn("Persona-authored step", skill.steps[0].prompt)

    def test_create_skill_rejects_duplicate(self) -> None:
        set_active_personality("coach")
        args = {
            "name": "dup-skill",
            "description": "First copy.",
            "body": self.GENERIC_BODY,
        }
        first = execute_skill_tool(self.memory_root, "coach", "create_skill", args)
        self.assertIn("Created skill", first.output)

        second = execute_skill_tool(self.memory_root, "coach", "create_skill", args)
        self.assertIn("Error", second.output)
        self.assertIn("already exists", second.output)

    def test_update_and_delete_skill(self) -> None:
        set_active_personality("coach")
        execute_skill_tool(
            self.memory_root,
            "coach",
            "create_skill",
            {
                "name": "temp-skill",
                "description": "Original description.",
                "body": self.GENERIC_BODY,
            },
        )

        updated = execute_skill_tool(
            self.memory_root,
            "coach",
            "update_skill",
            {
                "name": "temp-skill",
                "description": "Updated description.",
            },
        )
        self.assertIn("Updated skill", updated.output)
        skill = get_skill_definition(get_personality("coach"), "temp-skill")
        self.assertIn("Updated description", skill.description)

        deleted = execute_skill_tool(
            self.memory_root,
            "coach",
            "delete_skill",
            {"name": "temp-skill"},
        )
        self.assertIn("Deleted skill", deleted.output)
        with self.assertRaises(FileNotFoundError):
            get_skill_definition(get_personality("coach"), "temp-skill")

    def test_cannot_modify_builtin_skill(self) -> None:
        set_active_personality("buddy")
        result = execute_skill_tool(
            self.memory_root,
            "buddy",
            "update_skill",
            {"name": "edit-personality", "description": "Nope."},
        )
        self.assertIn("Error", result.output)

        delete_result = execute_skill_tool(
            self.memory_root,
            "buddy",
            "delete_skill",
            {"name": "edit-personality"},
        )
        self.assertIn("Error", delete_result.output)

    def test_repo_create_skill_builtin_is_valid(self) -> None:
        project_root = Path(__file__).resolve().parent.parent
        skill_dir = project_root / "skills" / "create-skill"
        skill = load_skill_definition(skill_dir, source="builtin")
        self.assertEqual(skill.name, "create-skill")
        self.assertEqual(skill.skill_type, "checklist")
        self.assertGreaterEqual(len(skill.steps), 4)
        self.assertIn("create_skill", skill.body)


if __name__ == "__main__":
    unittest.main()
