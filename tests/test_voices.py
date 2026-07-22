"""Tests for buddy_tools.voice.voices."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import buddy_tools.voice.voices as voices_module
from buddy_tools.voice.voices import (
    DEFAULT_VOICE_ID,
    VoiceProfile,
    get_voice,
    list_voices,
    resolve_voice,
    resolve_voice_audio_path,
    set_voices_dir,
)


class VoiceManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_voices_dir = voices_module.get_voices_dir()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.voices_root = Path(self._tmpdir.name)
        set_voices_dir(self.voices_root)

    def tearDown(self) -> None:
        set_voices_dir(self._original_voices_dir)
        self._tmpdir.cleanup()

    def _write_voice(
        self,
        voice_id: str,
        ref_text: str = "Hello, this is a test voice.",
        *,
        audio_name: str = "audio.wav",
    ) -> Path:
        voice_dir = self.voices_root / voice_id
        voice_dir.mkdir(parents=True)
        (voice_dir / audio_name).write_bytes(b"RIFF")
        (voice_dir / "ref_text.txt").write_text(ref_text, encoding="utf-8")
        return voice_dir

    def test_list_voices_discovers_complete_folders(self) -> None:
        self._write_voice("cliff")
        self._write_voice("narrator")
        incomplete = self.voices_root / "broken"
        incomplete.mkdir()
        (incomplete / "audio.wav").write_bytes(b"RIFF")

        self.assertEqual(list_voices(), ["cliff", "narrator"])

    def test_list_voices_discovers_flac_only_folders(self) -> None:
        self._write_voice("cliff", audio_name="audio.flac")
        self._write_voice("narrator", audio_name="audio.wav")

        self.assertEqual(list_voices(), ["cliff", "narrator"])

    def test_get_voice_returns_profile(self) -> None:
        self._write_voice("cliff", "Reference transcript here.")
        profile = get_voice("cliff")

        self.assertIsInstance(profile, VoiceProfile)
        self.assertEqual(profile.id, "cliff")
        self.assertEqual(profile.ref_text, "Reference transcript here.")
        self.assertEqual(profile.audio_path.name, "audio.wav")

    def test_get_voice_accepts_flac(self) -> None:
        self._write_voice("cliff", "FLAC transcript.", audio_name="audio.flac")
        profile = get_voice("cliff")

        self.assertEqual(profile.audio_path.name, "audio.flac")
        self.assertEqual(profile.ref_text, "FLAC transcript.")

    def test_prefers_wav_when_both_audio_files_exist(self) -> None:
        voice_dir = self._write_voice("cliff", "Both formats.")
        (voice_dir / "audio.flac").write_bytes(b"fLaC")

        profile = get_voice("cliff")
        resolved = resolve_voice_audio_path(voice_dir)

        self.assertEqual(profile.audio_path.name, "audio.wav")
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.name, "audio.wav")

    def test_resolve_voice_returns_audio_and_text(self) -> None:
        self._write_voice("cliff", "Clone text.")
        audio, text = resolve_voice("cliff")

        self.assertEqual(audio.name, "audio.wav")
        self.assertEqual(text, "Clone text.")

    def test_resolve_voice_defaults_to_cliff(self) -> None:
        self._write_voice(DEFAULT_VOICE_ID, "Default voice.")
        audio, text = resolve_voice()

        self.assertEqual(text, "Default voice.")
        self.assertTrue(audio.is_file())

    def test_missing_ref_text_raises(self) -> None:
        voice_dir = self.voices_root / "cliff"
        voice_dir.mkdir()
        (voice_dir / "audio.wav").write_bytes(b"RIFF")

        with self.assertRaises(FileNotFoundError):
            get_voice("cliff")

    def test_empty_ref_text_raises(self) -> None:
        self._write_voice("cliff", "   ")

        with self.assertRaises(ValueError):
            get_voice("cliff")

    def test_invalid_voice_id_raises(self) -> None:
        with self.assertRaises(ValueError):
            get_voice("")

        with self.assertRaises(ValueError):
            get_voice("!!!")

    def test_sanitize_voice_id_normalizes_spaces(self) -> None:
        self._write_voice("my_voice", "Spaced id.")
        profile = get_voice("My Voice")

        self.assertEqual(profile.id, "my_voice")


class ProjectVoicesTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_voices_dir = voices_module.get_voices_dir()

    def tearDown(self) -> None:
        set_voices_dir(self._original_voices_dir)

    def test_default_cliff_voice_exists(self) -> None:
        repo_voices = Path(__file__).resolve().parent.parent / "voices"
        set_voices_dir(repo_voices)
        profile = get_voice(DEFAULT_VOICE_ID)

        self.assertTrue(profile.audio_path.is_file())
        self.assertGreater(len(profile.ref_text), 10)


if __name__ == "__main__":
    unittest.main()
