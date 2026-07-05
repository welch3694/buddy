"""Tests for runtime voice switching (#6)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import buddy_tools.voice.voices as voices_module
from buddy_tools.core.patch import apply_patches
from buddy_tools.voice.session import apply_startup_voice, apply_voice, get_tts_handler, set_tts_handler
from buddy_tools.voice.voices import ref_text_for_audio_path, set_voices_dir
from speech_to_speech.api.openai_realtime.runtime_config import RuntimeConfig


class RefTextForAudioPathTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_voices_dir = voices_module.get_voices_dir()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.voices_root = Path(self._tmpdir.name)
        set_voices_dir(self.voices_root)

    def tearDown(self) -> None:
        set_voices_dir(self._original_voices_dir)
        self._tmpdir.cleanup()

    def test_loads_ref_text_from_voice_folder(self) -> None:
        voice_dir = self.voices_root / "cliff"
        voice_dir.mkdir()
        (voice_dir / "audio.wav").write_bytes(b"RIFF")
        (voice_dir / "ref_text.txt").write_text("Matching transcript.", encoding="utf-8")

        ref_text = ref_text_for_audio_path(voice_dir / "audio.wav")

        self.assertEqual(ref_text, "Matching transcript.")

    def test_returns_none_for_non_voice_paths(self) -> None:
        path = self.voices_root / "other.wav"
        path.write_bytes(b"RIFF")
        self.assertIsNone(ref_text_for_audio_path(path))


class ApplyVoiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_voices_dir = voices_module.get_voices_dir()
        self._original_tts_handler = get_tts_handler()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.voices_root = Path(self._tmpdir.name)
        set_voices_dir(self.voices_root)
        self._write_voice("cliff", "Cliff transcript.")
        self._write_voice("narrator", "Narrator transcript.")

    def tearDown(self) -> None:
        set_voices_dir(self._original_voices_dir)
        set_tts_handler(self._original_tts_handler)
        self._tmpdir.cleanup()

    def _write_voice(self, voice_id: str, ref_text: str) -> None:
        voice_dir = self.voices_root / voice_id
        voice_dir.mkdir(parents=True, exist_ok=True)
        (voice_dir / "audio.wav").write_bytes(b"RIFF")
        (voice_dir / "ref_text.txt").write_text(ref_text, encoding="utf-8")

    def test_apply_voice_updates_handler_and_runtime_config(self) -> None:
        handler = Mock()
        handler.__class__.__name__ = "Qwen3TTSHandler"
        handler.ref_audio = None
        handler.ref_text = "old"
        runtime_config = RuntimeConfig()

        profile = apply_voice("narrator", runtime_config=runtime_config, tts_handler=handler)

        self.assertEqual(profile.id, "narrator")
        self.assertEqual(handler.ref_text, "Narrator transcript.")
        self.assertTrue(str(handler.ref_audio).endswith("narrator\\audio.wav") or str(handler.ref_audio).endswith("narrator/audio.wav"))
        self.assertIn("narrator", runtime_config.session.audio.output.voice)

    def test_apply_voice_uses_registered_handler_when_available(self) -> None:
        handler = Mock()
        handler.__class__.__name__ = "Qwen3TTSHandler"
        set_tts_handler(handler)

        apply_voice("cliff")

        self.assertEqual(handler.ref_text, "Cliff transcript.")

    def test_apply_voice_updates_pocket_handler(self) -> None:
        voice_state = object()
        model = Mock()
        model.get_state_for_audio_prompt.return_value = voice_state
        handler = Mock()
        handler.__class__.__name__ = "PocketTTSHandler"
        handler.model = model
        handler.voice = "old"
        runtime_config = RuntimeConfig()

        profile = apply_voice("cliff", runtime_config=runtime_config, tts_handler=handler)

        self.assertEqual(profile.id, "cliff")
        self.assertTrue(str(handler.voice).endswith("cliff\\audio.wav") or str(handler.voice).endswith("cliff/audio.wav"))
        self.assertIs(handler.voice_state, voice_state)
        model.get_state_for_audio_prompt.assert_called_once()


class Qwen3PatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_voices_dir = voices_module.get_voices_dir()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.voices_root = Path(self._tmpdir.name)
        set_voices_dir(self.voices_root)
        voice_dir = self.voices_root / "cliff"
        voice_dir.mkdir()
        (voice_dir / "audio.wav").write_bytes(b"RIFF")
        (voice_dir / "ref_text.txt").write_text("Patched transcript.", encoding="utf-8")
        self.audio_path = voice_dir / "audio.wav"

    def tearDown(self) -> None:
        set_voices_dir(self._original_voices_dir)
        self._tmpdir.cleanup()

    def test_patched_override_syncs_ref_text_from_voice_folder(self) -> None:
        apply_patches()
        from speech_to_speech.TTS.qwen3_tts_handler import Qwen3TTSHandler

        handler = object.__new__(Qwen3TTSHandler)
        handler.ref_audio = None
        handler.ref_text = "stale"
        handler._resolve_audio_path = lambda candidate: Path(candidate) if Path(candidate).exists() else None

        runtime_config = RuntimeConfig()
        runtime_config.session.audio.output.voice = str(self.audio_path)

        handler._apply_session_voice_override("voice_clone", runtime_config=runtime_config)

        self.assertEqual(str(handler.ref_audio), str(self.audio_path))
        self.assertEqual(handler.ref_text, "Patched transcript.")

    def test_apply_patches_is_idempotent(self) -> None:
        apply_patches()
        from speech_to_speech.TTS.qwen3_tts_handler import Qwen3TTSHandler

        first = Qwen3TTSHandler._apply_session_voice_override
        apply_patches()
        second = Qwen3TTSHandler._apply_session_voice_override
        self.assertIs(first, second)

    def test_patched_process_voice_clone_passes_cached_prompt(self) -> None:
        apply_patches()
        from speech_to_speech.TTS.qwen3_tts_handler import Qwen3TTSHandler

        handler = object.__new__(Qwen3TTSHandler)
        handler.backend = "faster_qwen3_tts"
        handler.ref_audio = self.audio_path
        handler.ref_text = "Patched transcript."
        handler.language = "auto"
        handler.xvec_only = False
        handler.streaming_chunk_size = 8
        handler.parity_mode = False
        handler.non_streaming_mode = True
        handler.voice_clone_prompt = ["cached-prompt"]
        handler._estimate_max_new_tokens = lambda _text: 512
        handler._stream = lambda gen, label: list(gen)
        handler.model = Mock()
        handler.model.generate_voice_clone_streaming = Mock(return_value=iter(()))

        list(handler._process_voice_clone("Hello there."))

        kwargs = handler.model.generate_voice_clone_streaming.call_args.kwargs
        self.assertEqual(kwargs["voice_clone_prompt"], ["cached-prompt"])
        self.assertEqual(kwargs["ref_audio"], self.audio_path)
        self.assertEqual(kwargs["ref_text"], "Patched transcript.")


if __name__ == "__main__":
    unittest.main()
