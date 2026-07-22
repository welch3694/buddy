"""Named voice clone profiles for Qwen3 TTS Base (audio.wav|flac + ref_text.txt)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

DEFAULT_VOICE_ID = "cliff"
AUDIO_WAV_FILENAME = "audio.wav"
AUDIO_FLAC_FILENAME = "audio.flac"
# Prefer WAV when both exist (existing convention).
AUDIO_FILENAMES = (AUDIO_WAV_FILENAME, AUDIO_FLAC_FILENAME)
AUDIO_FILENAME = AUDIO_WAV_FILENAME  # backward-compatible alias
REF_TEXT_FILENAME = "ref_text.txt"

_SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_AUDIO_NAMES = frozenset(AUDIO_FILENAMES)

_VOICES_DIR = Path(__file__).resolve().parent.parent.parent / "voices"


@dataclass(frozen=True)
class VoiceProfile:
    id: str
    audio_path: Path
    ref_text: str


def get_voices_dir() -> Path:
    return _VOICES_DIR


def set_voices_dir(path: Path) -> None:
    global _VOICES_DIR
    _VOICES_DIR = path.resolve()


def _sanitize_voice_id(voice_id: str) -> str:
    cleaned = voice_id.strip().lower().replace(" ", "_")
    cleaned = re.sub(r"[^a-z0-9_-]", "", cleaned)
    if not cleaned or not _SAFE_NAME.match(cleaned):
        raise ValueError(f"Invalid voice id: {voice_id!r}")
    return cleaned


def _voices_root(voices_dir: Path | None = None) -> Path:
    return (voices_dir or _VOICES_DIR).resolve()


def _voice_dir(voices_root: Path, voice_id: str) -> Path:
    sanitized = _sanitize_voice_id(voice_id)
    path = (voices_root / sanitized).resolve()
    if path.parent != voices_root:
        raise ValueError(f"Invalid voice id: {voice_id!r}")
    return path


def resolve_voice_audio_path(directory: Path) -> Path | None:
    """Return audio.wav or audio.flac in directory; prefer WAV when both exist."""
    for name in AUDIO_FILENAMES:
        path = directory / name
        if path.is_file():
            return path
    return None


def is_valid_voice_dir(path: Path) -> bool:
    """True when the folder has ref_text.txt and either audio.wav or audio.flac."""
    return resolve_voice_audio_path(path) is not None and (path / REF_TEXT_FILENAME).is_file()


def list_voices(voices_dir: Path | None = None) -> list[str]:
    """Return ids of voices that have audio.wav or audio.flac plus ref_text.txt."""
    root = _voices_root(voices_dir)
    if not root.is_dir():
        return []
    return sorted(
        entry.name
        for entry in root.iterdir()
        if entry.is_dir() and _SAFE_NAME.match(entry.name) and is_valid_voice_dir(entry)
    )


def get_voice(voice_id: str, voices_dir: Path | None = None) -> VoiceProfile:
    """Load a voice profile, validating that clone audio and ref text exist."""
    root = _voices_root(voices_dir)
    sanitized = _sanitize_voice_id(voice_id)
    directory = _voice_dir(root, sanitized)

    ref_text_path = directory / REF_TEXT_FILENAME

    if not directory.is_dir():
        raise FileNotFoundError(f"Voice {sanitized!r} not found under {root}")

    audio_path = resolve_voice_audio_path(directory)
    if audio_path is None:
        raise FileNotFoundError(
            f"Voice {sanitized!r} missing {AUDIO_WAV_FILENAME} or {AUDIO_FLAC_FILENAME} in {directory}"
        )
    if not ref_text_path.is_file():
        raise FileNotFoundError(f"Voice {sanitized!r} missing {REF_TEXT_FILENAME} in {directory}")

    ref_text = ref_text_path.read_text(encoding="utf-8").strip()
    if not ref_text:
        raise ValueError(f"Voice {sanitized!r} has empty {REF_TEXT_FILENAME}")

    return VoiceProfile(
        id=sanitized,
        audio_path=audio_path.resolve(),
        ref_text=ref_text,
    )


def resolve_voice(voice_id: str | None = None, voices_dir: Path | None = None) -> tuple[Path, str]:
    """Return (audio_path, ref_text) for TTS voice cloning."""
    profile = get_voice(voice_id or DEFAULT_VOICE_ID, voices_dir)
    return profile.audio_path, profile.ref_text


def ref_text_for_audio_path(audio_path: str | Path) -> str | None:
    """Load ref_text.txt from the same voice folder as audio.wav/flac, if present."""
    path = Path(audio_path)
    try:
        resolved = path.resolve()
    except OSError:
        return None

    if resolved.name not in _AUDIO_NAMES:
        return None

    ref_text_path = resolved.parent / REF_TEXT_FILENAME
    if not ref_text_path.is_file():
        return None

    ref_text = ref_text_path.read_text(encoding="utf-8").strip()
    return ref_text or None
