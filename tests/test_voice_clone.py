"""Tests for voice clone prompt precomputation."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import buddy_tools.voice.voices as voices_module
from buddy_tools.voice.clone import (
    precompute_voice_clone_prompt,
    refresh_voice_clone_prompt,
    voice_clone_log_context,
)
from buddy_tools.voice.session import apply_startup_voice
from buddy_tools.voice.voices import set_voices_dir
from speech_to_speech.api.openai_realtime.runtime_config import RuntimeConfig


class VoiceClonePromptTests(unittest.TestCase):
    def test_precompute_returns_none_without_model(self) -> None:
        handler = Mock(backend="faster_qwen3_tts", ref_audio="/voices/cliff/audio.wav", ref_text="Hello.")
        handler.model = None

        self.assertIsNone(precompute_voice_clone_prompt(handler))

    def test_precompute_builds_prompt_from_faster_backend(self) -> None:
        prompt_items = [Mock()]
        base_model = Mock()
        base_model.create_voice_clone_prompt.return_value = prompt_items
        model_wrapper = Mock()
        model_wrapper.model = base_model
        model_wrapper._load_ref_audio_with_silence.return_value = "normalized-audio"
        handler = Mock(
            backend="faster_qwen3_tts",
            ref_audio="/voices/cliff/audio.wav",
            ref_text="Reference transcript.",
            xvec_only=False,
            model=model_wrapper,
        )

        result = precompute_voice_clone_prompt(handler)

        self.assertIs(result, prompt_items)
        model_wrapper._load_ref_audio_with_silence.assert_called_once()
        base_model.create_voice_clone_prompt.assert_called_once_with(
            ref_audio="normalized-audio",
            ref_text="Reference transcript.",
        )

    def test_refresh_stores_prompt_on_handler(self) -> None:
        handler = Mock(
            backend="faster_qwen3_tts",
            ref_audio="/voices/cliff/audio.wav",
            ref_text="Reference transcript.",
            xvec_only=False,
        )
        handler.model = Mock(model=Mock(create_voice_clone_prompt=Mock(return_value=["prompt"])))
        handler.model._load_ref_audio_with_silence.return_value = "normalized-audio"

        self.assertTrue(refresh_voice_clone_prompt(handler))
        self.assertEqual(handler.voice_clone_prompt, ["prompt"])

    def test_voice_clone_log_context_includes_cached_flag(self) -> None:
        handler = Mock(
            ref_audio=Path("/voices/cliff/audio.wav"),
            ref_text="Short transcript.",
            voice_clone_prompt=["cached"],
        )

        context = voice_clone_log_context(handler)

        self.assertIn("cached_prompt=True", context)
        self.assertIn("audio.wav", context)


class ApplyStartupVoiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_voices_dir = voices_module.get_voices_dir()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.voices_root = Path(self._tmpdir.name) / "voices"
        self.voices_root.mkdir()
        set_voices_dir(self.voices_root)
        voice_dir = self.voices_root / "cliff"
        voice_dir.mkdir()
        (voice_dir / "audio.wav").write_bytes(b"RIFF")
        (voice_dir / "ref_text.txt").write_text("Cliff transcript.", encoding="utf-8")

    def tearDown(self) -> None:
        set_voices_dir(self._original_voices_dir)
        self._tmpdir.cleanup()

    @patch("buddy_tools.voice.session.get_active_personality")
    @patch("buddy_tools.voice.session.get_tts_handler")
    def test_apply_startup_voice_wires_session_and_handler(
        self,
        mock_get_handler: Mock,
        mock_get_active: Mock,
    ) -> None:
        from buddy_tools.personality import PersonalityProfile

        mock_get_active.return_value = PersonalityProfile(
            id="buddy",
            name="Buddy",
            description="",
            voice_id="cliff",
            behaviors={},
            memory_namespace="buddy",
            prompt="You are Buddy.",
            directory=Path("personalities/buddy"),
        )
        handler = Mock()
        handler.__class__.__name__ = "Qwen3TTSHandler"
        handler.ref_audio = None
        handler.ref_text = "old"
        mock_get_handler.return_value = handler
        runtime_config = RuntimeConfig()

        profile = apply_startup_voice(runtime_config=runtime_config)

        self.assertIsNotNone(profile)
        assert profile is not None
        self.assertEqual(profile.id, "cliff")
        self.assertEqual(handler.ref_text, "Cliff transcript.")
        self.assertIn("cliff", runtime_config.session.audio.output.voice)


if __name__ == "__main__":
    unittest.main()
