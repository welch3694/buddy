"""Tests for Buddy voice-prompt anti-fabrication patch (#142)."""

from __future__ import annotations

import unittest

from buddy_tools.core.patch import _patch_voice_prompt_anti_fabrication


class VoicePromptAntiFabricationTests(unittest.TestCase):
    def test_patched_voice_prompt_appends_buddy_tool_rules(self) -> None:
        _patch_voice_prompt_anti_fabrication()
        from speech_to_speech.LLM.voice_prompt import build_voice_system_prompt

        prompt = build_voice_system_prompt("You are Buddy.")

        self.assertIn("## Voice Rules", prompt)
        self.assertIn("If unsure whether a tool is needed, just speak.", prompt)
        self.assertIn("## Buddy Tool Rules", prompt)
        self.assertIn("Never claim you started a skill", prompt)
        self.assertIn("do not just speak a success claim", prompt)
        # Buddy override must come after s2s voice rules (strongest last).
        self.assertGreater(
            prompt.index("## Buddy Tool Rules"),
            prompt.index("If unsure whether a tool is needed, just speak."),
        )

    def test_voice_prompt_patch_is_idempotent(self) -> None:
        _patch_voice_prompt_anti_fabrication()
        from speech_to_speech.LLM import voice_prompt

        first = voice_prompt.build_voice_system_prompt
        _patch_voice_prompt_anti_fabrication()
        second = voice_prompt.build_voice_system_prompt
        self.assertIs(first, second)

        prompt = voice_prompt.build_voice_system_prompt("Session.")
        self.assertEqual(prompt.count("## Buddy Tool Rules"), 1)


if __name__ == "__main__":
    unittest.main()
