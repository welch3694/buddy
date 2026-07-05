"""Tests for namespaced persistent memory."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from buddy_tools.memory import (
    execute_memory_tool,
    load_memory_summary,
    migrate_legacy_memory,
    persona_memory_dir,
)
from buddy_tools.personality.session import apply_personality_switch
from buddy_tools.core.registry import execute_tool
from speech_to_speech.api.openai_realtime.runtime_config import RuntimeConfig


class MemoryNamespaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.memory_root = Path(self._tmpdir.name) / "memory"
        self.memory_root.mkdir()

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_migrate_legacy_notes_to_global(self) -> None:
        legacy = self.memory_root / "notes.md"
        legacy.write_text("# Notes\n- Favorite color: blue\n", encoding="utf-8")

        migrated = migrate_legacy_memory(self.memory_root)

        self.assertTrue(migrated)
        self.assertFalse(legacy.exists())
        global_notes = self.memory_root / "global" / "notes.md"
        self.assertTrue(global_notes.is_file())
        self.assertIn("Favorite color: blue", global_notes.read_text(encoding="utf-8"))

    def test_load_memory_summary_includes_global_and_active_persona_only(self) -> None:
        (self.memory_root / "global").mkdir()
        (self.memory_root / "buddy").mkdir()
        (self.memory_root / "coach").mkdir()
        (self.memory_root / "global" / "notes.md").write_text(
            "- User name: Alex\n", encoding="utf-8"
        )
        (self.memory_root / "buddy" / "notes.md").write_text(
            "- Last topic: weather\n", encoding="utf-8"
        )
        (self.memory_root / "coach" / "notes.md").write_text(
            "- Workout plan: legs\n", encoding="utf-8"
        )

        buddy_summary = load_memory_summary(self.memory_root, "buddy")
        coach_summary = load_memory_summary(self.memory_root, "coach")

        self.assertIn("User name: Alex", buddy_summary)
        self.assertIn("Last topic: weather", buddy_summary)
        self.assertNotIn("Workout plan: legs", buddy_summary)

        self.assertIn("User name: Alex", coach_summary)
        self.assertIn("Workout plan: legs", coach_summary)
        self.assertNotIn("Last topic: weather", coach_summary)

    def test_memory_tools_scope_global_vs_persona(self) -> None:
        result = execute_memory_tool(
            self.memory_root,
            "buddy",
            "update_memory",
            {"name": "notes", "topic": "user name", "value": "Sam", "scope": "global"},
        )
        self.assertIn("global/notes", result.output)

        execute_memory_tool(
            self.memory_root,
            "buddy",
            "update_memory",
            {"name": "notes", "topic": "focus", "value": "small talk"},
        )
        execute_memory_tool(
            self.memory_root,
            "coach",
            "update_memory",
            {"name": "notes", "topic": "focus", "value": "fitness"},
        )

        listed = json.loads(
            execute_memory_tool(self.memory_root, "buddy", "list_memory", {}).output
        )
        self.assertEqual(listed["global"], ["notes"])
        self.assertEqual(listed["persona"], ["notes"])

        buddy_persona = persona_memory_dir(self.memory_root, "buddy") / "notes.md"
        coach_persona = persona_memory_dir(self.memory_root, "coach") / "notes.md"
        self.assertIn("Sam", (self.memory_root / "global" / "notes.md").read_text(encoding="utf-8"))
        self.assertIn("small talk", buddy_persona.read_text(encoding="utf-8"))
        self.assertIn("fitness", coach_persona.read_text(encoding="utf-8"))

    def test_execute_tool_requires_persona_namespace(self) -> None:
        result = execute_tool(
            self.memory_root,
            "list_memory",
            "{}",
            persona_namespace="buddy",
        )
        payload = json.loads(result.output)
        self.assertIn("global", payload)
        self.assertIn("persona", payload)


class MemoryPersonalitySwitchTests(unittest.TestCase):
    def setUp(self) -> None:
        from buddy_tools import personality as personality_module
        import buddy_tools.voice.voices as voices_module
        from buddy_tools.personality import create_personality, set_active_personality, set_personalities_dir
        from buddy_tools.voice.voices import set_voices_dir

        self._original_personalities_dir = personality_module.get_personalities_dir()
        self._original_voices_dir = voices_module.get_voices_dir()
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
        for voice_id in ("cliff", "narrator"):
            voice_dir = self.voices_root / voice_id
            voice_dir.mkdir()
            (voice_dir / "audio.wav").write_bytes(b"RIFF")
            (voice_dir / "ref_text.txt").write_text(f"{voice_id} transcript", encoding="utf-8")
        create_personality("buddy", "Buddy", "You are Buddy.", voice_id="cliff")
        create_personality("coach", "Coach", "You are Coach.", voice_id="narrator")
        set_active_personality("buddy")

        (self.memory_root / "global").mkdir()
        (self.memory_root / "buddy").mkdir()
        (self.memory_root / "coach").mkdir()
        (self.memory_root / "global" / "notes.md").write_text(
            "- User name: Alex\n", encoding="utf-8"
        )
        (self.memory_root / "buddy" / "notes.md").write_text(
            "- Buddy mood: cheerful\n", encoding="utf-8"
        )
        (self.memory_root / "coach" / "notes.md").write_text(
            "- Coach focus: cardio\n", encoding="utf-8"
        )

    def tearDown(self) -> None:
        from buddy_tools.personality import set_personalities_dir
        from buddy_tools.voice.voices import set_voices_dir

        set_personalities_dir(self._original_personalities_dir)
        set_voices_dir(self._original_voices_dir)
        self._tmpdir.cleanup()

    def test_personality_switch_refreshes_persona_memory_in_instructions(self) -> None:
        from speech_to_speech.LLM.chat import Chat

        runtime_config = RuntimeConfig()
        chat = Chat(10)

        apply_personality_switch(
            "coach",
            runtime_config=runtime_config,
            chat=chat,
            memory_root=self.memory_root,
        )

        instructions = runtime_config.session.instructions
        self.assertIn("User name: Alex", instructions)
        self.assertIn("Coach focus: cardio", instructions)
        self.assertNotIn("Buddy mood: cheerful", instructions)


if __name__ == "__main__":
    unittest.main()
