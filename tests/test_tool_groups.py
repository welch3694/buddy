"""Tests for tool groups, routing table, and per-persona visibility."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from speech_to_speech.LLM.chat import Chat
from speech_to_speech.api.openai_realtime.runtime_config import RuntimeConfig

from buddy_tools import personality as personality_module
import buddy_tools.voice.voices as voices_module
from buddy_tools.core.groups import (
    ToolGroup,
    flatten_tool_definitions,
    resolve_visible_groups,
    visible_tool_definitions,
)
from buddy_tools.core.registry import (
    ALL_TOOL_DEFINITIONS,
    TOOL_GROUPS,
    build_tool_instructions,
    tools_for_personality,
)
from buddy_tools.infra.bootstrap import configure_runtime_tools
from buddy_tools.personality import (
    PersonalityProfile,
    create_personality,
    set_active_personality,
    set_personalities_dir,
    update_personality,
)
from buddy_tools.personality.session import apply_personality_switch
from buddy_tools.voice.session import set_tts_handler
from buddy_tools.voice.voices import set_voices_dir


class ToolGroupRegistrationTests(unittest.TestCase):
    def test_expected_group_ids_registered(self) -> None:
        ids = {group.id for group in TOOL_GROUPS}
        self.assertEqual(
            ids,
            {
                "persona",
                "persona_admin",
                "theme",
                "memory",
                "episodic",
                "skills",
                "timers",
                "vision",
                "channels",
            },
        )

    def test_all_tool_definitions_derived_from_groups(self) -> None:
        flattened = flatten_tool_definitions(TOOL_GROUPS)
        self.assertEqual([t.name for t in ALL_TOOL_DEFINITIONS], [t.name for t in flattened])
        names = [t.name for t in ALL_TOOL_DEFINITIONS]
        self.assertEqual(len(names), len(set(names)))

    def test_persona_admin_is_hidden_by_default(self) -> None:
        admin = next(g for g in TOOL_GROUPS if g.id == "persona_admin")
        self.assertFalse(admin.default_visible)
        self.assertTrue(admin.admin_only)


class ToolRoutingInstructionTests(unittest.TestCase):
    def test_routing_table_and_identity_rule(self) -> None:
        text = build_tool_instructions("Base prompt.", "(no memory saved yet)")
        self.assertIn("## Tool routing", text)
        lowered = text.lower()
        for needle in ("persona", "memory", "episodic", "skills", "vision", "timers", "channels"):
            self.assertIn(needle, lowered)
        self.assertIn("switch_personality", text)
        self.assertIn("Identity rule", text)
        self.assertIn("Never impersonate", text)
        self.assertIn("update_personality", text)
        self.assertIn("send_telegram_message", text)
        self.assertIn("send_telegram_photo", text)
        self.assertIn("speak_aloud", text)
        self.assertNotIn("create_personality", text)

    def test_active_context_and_admin_for_buddy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            personalities = root / "personalities"
            voices = root / "voices"
            personalities.mkdir()
            voices.mkdir()
            original_p = personality_module.get_personalities_dir()
            original_v = voices_module.get_voices_dir()
            try:
                set_personalities_dir(personalities)
                set_voices_dir(voices)
                voice_dir = voices / "cliff"
                voice_dir.mkdir()
                (voice_dir / "audio.wav").write_bytes(b"RIFF")
                (voice_dir / "ref_text.txt").write_text("cliff", encoding="utf-8")
                create_personality("buddy", "Buddy", "You are Buddy.", voice_id="cliff")

                text = build_tool_instructions(
                    "Base prompt.",
                    "(no memory saved yet)",
                    personality_id="buddy",
                )
                self.assertIn("## Active context", text)
                self.assertIn("id: buddy", text)
                self.assertIn("create_personality", text)
                self.assertIn("## Persona admin", text)
            finally:
                set_personalities_dir(original_p)
                set_voices_dir(original_v)


class ToolVisibilityTests(unittest.TestCase):
    def _profile(
        self,
        personality_id: str = "coach",
        *,
        tool_groups: tuple[str, ...] = (),
    ) -> PersonalityProfile:
        return PersonalityProfile(
            id=personality_id,
            name=personality_id.title(),
            description="",
            voice_id="cliff",
            behaviors={},
            memory_namespace=personality_id,
            prompt="You are a test persona.",
            directory=Path("personalities") / personality_id,
            tool_groups=tool_groups,
        )

    def test_non_buddy_hides_persona_admin(self) -> None:
        names = {t.name for t in visible_tool_definitions(TOOL_GROUPS, self._profile())}
        self.assertIn("switch_personality", names)
        self.assertIn("update_personality", names)
        self.assertNotIn("create_personality", names)
        self.assertNotIn("delete_personality", names)

    def test_buddy_gets_persona_admin(self) -> None:
        names = {t.name for t in tools_for_personality(self._profile("buddy"))}
        self.assertIn("create_personality", names)
        self.assertIn("update_personality", names)
        self.assertIn("delete_personality", names)

    def test_opt_in_via_tool_groups(self) -> None:
        names = {
            t.name
            for t in visible_tool_definitions(
                TOOL_GROUPS,
                self._profile(tool_groups=("persona_admin",)),
            )
        }
        self.assertIn("create_personality", names)

    def test_unknown_tool_group_raises(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            resolve_visible_groups(TOOL_GROUPS, self._profile(tool_groups=("not_a_group",)))
        self.assertIn("unknown tool_groups", str(ctx.exception))


class RuntimeToolFilterTests(unittest.TestCase):
    def setUp(self) -> None:
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

    def tearDown(self) -> None:
        set_personalities_dir(self._original_personalities_dir)
        set_voices_dir(self._original_voices_dir)
        self._tmpdir.cleanup()

    def test_configure_runtime_tools_filters_for_active_persona(self) -> None:
        set_active_personality("coach")
        runtime_config = RuntimeConfig()
        configure_runtime_tools(runtime_config, self.memory_root)
        names = {t.name for t in runtime_config.session.tools}
        self.assertIn("switch_personality", names)
        self.assertNotIn("create_personality", names)
        self.assertIn("## Tool routing", runtime_config.session.instructions)
        self.assertIn("Identity rule", runtime_config.session.instructions)

    def test_switch_refilters_session_tools(self) -> None:
        runtime_config = RuntimeConfig()
        chat = Chat(10)
        handler = Mock()
        handler.__class__.__name__ = "Qwen3TTSHandler"
        handler.ref_audio = None
        handler.ref_text = "old"
        set_tts_handler(handler)

        configure_runtime_tools(runtime_config, self.memory_root)
        self.assertIn("create_personality", {t.name for t in runtime_config.session.tools})

        apply_personality_switch(
            "coach",
            runtime_config=runtime_config,
            chat=chat,
            memory_root=self.memory_root,
        )
        names = {t.name for t in runtime_config.session.tools}
        self.assertNotIn("create_personality", names)
        self.assertIn("switch_personality", names)

        update_personality("coach", tool_groups=["persona_admin"])
        apply_personality_switch(
            "coach",
            runtime_config=runtime_config,
            chat=chat,
            memory_root=self.memory_root,
        )
        self.assertIn("create_personality", {t.name for t in runtime_config.session.tools})


class ToolGroupHelperTests(unittest.TestCase):
    def test_flatten_rejects_duplicate_tool_names(self) -> None:
        from openai.types.realtime import RealtimeFunctionTool

        tool = RealtimeFunctionTool(
            type="function",
            name="dup",
            description="x",
            parameters={"type": "object", "properties": {}},
        )
        groups = (
            ToolGroup(id="a", title="A", when_to_use="a", tools=(tool,), instructions="a"),
            ToolGroup(id="b", title="B", when_to_use="b", tools=(tool,), instructions="b"),
        )
        with self.assertRaises(ValueError):
            flatten_tool_definitions(groups)


if __name__ == "__main__":
    unittest.main()
