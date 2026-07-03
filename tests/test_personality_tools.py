"""Tests for personality tools and session switching."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from buddy_tools import personality as personality_module
from buddy_tools import voices as voices_module
from buddy_tools.personality import create_personality, set_active_personality, set_personalities_dir
from buddy_tools.personality_session import apply_personality_switch, reset_chat_history
from buddy_tools.personality_tools import execute_personality_tool
from buddy_tools.registry import ALL_TOOL_DEFINITIONS, build_tool_instructions, execute_tool
from buddy_tools.voice_session import set_tts_handler
from buddy_tools.voices import set_voices_dir
from speech_to_speech.LLM.chat import Chat
from speech_to_speech.api.openai_realtime.runtime_config import RuntimeConfig


class PersonalityToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_personalities_dir = personality_module.get_personalities_dir()
        self._original_voices_dir = voices_module.get_voices_dir()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.personalities_root = self.root / "personalities"
        self.voices_root = self.root / "voices"
        self.memory_dir = self.root / "memory"
        self.personalities_root.mkdir()
        self.voices_root.mkdir()
        self.memory_dir.mkdir()
        set_personalities_dir(self.personalities_root)
        set_voices_dir(self.voices_root)
        self._write_voice("cliff")
        self._write_voice("narrator")
        create_personality("buddy", "Buddy", "You are Buddy.", voice_id="cliff")
        create_personality("coach", "Coach", "You are Coach.", voice_id="narrator")

    def tearDown(self) -> None:
        set_personalities_dir(self._original_personalities_dir)
        set_voices_dir(self._original_voices_dir)
        self._tmpdir.cleanup()

    def _write_voice(self, voice_id: str) -> None:
        voice_dir = self.voices_root / voice_id
        voice_dir.mkdir(parents=True, exist_ok=True)
        (voice_dir / "audio.wav").write_bytes(b"RIFF")
        (voice_dir / "ref_text.txt").write_text(f"{voice_id} transcript", encoding="utf-8")

    def test_registry_includes_personality_tools(self) -> None:
        names = {tool.name for tool in ALL_TOOL_DEFINITIONS}
        self.assertIn("switch_personality", names)
        self.assertIn("list_voices", names)

    def test_list_personalities_tool(self) -> None:
        result = execute_personality_tool("list_personalities", {})
        payload = json.loads(result.output)
        self.assertEqual(payload["active"], "buddy")
        self.assertIn("coach", payload["personalities"])

    def test_switch_personality_tool_requests_session_switch(self) -> None:
        result = execute_personality_tool("switch_personality", {"personality_id": "coach"})
        self.assertEqual(result.personality_switch_id, "coach")

    def test_create_and_delete_personality_tools(self) -> None:
        created = execute_personality_tool(
            "create_personality",
            {
                "personality_id": "guide",
                "name": "Guide",
                "prompt": "You are a calm guide.",
                "voice_id": "cliff",
            },
        )
        self.assertIn("Created personality Guide", created.output)

        deleted = execute_personality_tool("delete_personality", {"personality_id": "guide"})
        self.assertIn("Deleted personality guide", deleted.output)

    def test_execute_tool_dispatches_personality_tools(self) -> None:
        result = execute_tool(self.memory_dir, "list_voices", "{}")
        payload = json.loads(result.output)
        self.assertIn("cliff", payload["voices"])


class PersonalitySessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_personalities_dir = personality_module.get_personalities_dir()
        self._original_voices_dir = voices_module.get_voices_dir()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.personalities_root = self.root / "personalities"
        self.voices_root = self.root / "voices"
        self.memory_dir = self.root / "memory"
        self.personalities_root.mkdir()
        self.voices_root.mkdir()
        self.memory_dir.mkdir()
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

    def test_reset_chat_history_clears_buffer(self) -> None:
        chat = Chat(10)
        chat.buffer.append(Mock())
        chat._user_turn_count = 1

        reset_chat_history(chat)

        self.assertEqual(chat.buffer, [])
        self.assertEqual(chat._user_turn_count, 0)

    def test_apply_personality_switch_updates_session(self) -> None:
        runtime_config = RuntimeConfig()
        chat = Chat(10)
        chat.buffer.append(Mock())
        handler = Mock()
        handler.__class__.__name__ = "Qwen3TTSHandler"
        handler.ref_audio = None
        handler.ref_text = "old"
        set_tts_handler(handler)

        profile = apply_personality_switch(
            "coach",
            runtime_config=runtime_config,
            chat=chat,
            memory_dir=self.memory_dir,
        )

        self.assertEqual(profile.id, "coach")
        self.assertEqual(chat.buffer, [])
        self.assertIn("You are Coach.", runtime_config.session.instructions)
        self.assertIn("switch_personality", runtime_config.session.instructions)
        self.assertIn("narrator", runtime_config.session.audio.output.voice)
        self.assertEqual(handler.ref_text, "narrator transcript")

    def test_personality_switch_clears_pending_function_calls(self) -> None:
        """After switch, function_call is gone so tool output cannot be paired in chat."""
        from openai.types.realtime import RealtimeConversationItemFunctionCall

        runtime_config = RuntimeConfig()
        chat = Chat(10)
        chat.add_item(
            RealtimeConversationItemFunctionCall(
                type="function_call",
                name="switch_personality",
                arguments='{"personality_id":"coach"}',
                call_id="call_test",
            )
        )
        handler = Mock()
        handler.__class__.__name__ = "Qwen3TTSHandler"
        handler.ref_audio = None
        handler.ref_text = "old"
        set_tts_handler(handler)

        apply_personality_switch(
            "coach",
            runtime_config=runtime_config,
            chat=chat,
            memory_dir=self.memory_dir,
        )

        self.assertEqual(chat.buffer, [])

    def test_build_tool_instructions_includes_personality_help(self) -> None:
        text = build_tool_instructions("Base prompt.", "(no memory saved yet)")
        self.assertIn("switch_personality", text)
        self.assertIn("list_voices", text)


if __name__ == "__main__":
    unittest.main()
