"""Named voice clone profiles for Qwen3 TTS Base (audio.wav + ref_text.txt)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

DEFAULT_VOICE_ID = "cliff"
AUDIO_FILENAME = "audio.wav"
REF_TEXT_FILENAME = "ref_text.txt"

_SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

_VOICES_DIR = Path(__file__).resolve().parent.parent / "voices"


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


def _is_valid_voice_dir(path: Path) -> bool:
    return (path / AUDIO_FILENAME).is_file() and (path / REF_TEXT_FILENAME).is_file()


def list_voices(voices_dir: Path | None = None) -> list[str]:
    """Return ids of voices that have both audio.wav and ref_text.txt."""
    root = _voices_root(voices_dir)
    if not root.is_dir():
        return []
    return sorted(
        entry.name
        for entry in root.iterdir()
        if entry.is_dir() and _SAFE_NAME.match(entry.name) and _is_valid_voice_dir(entry)
    )


def get_voice(voice_id: str, voices_dir: Path | None = None) -> VoiceProfile:
    """Load a voice profile, validating that both clone files exist."""
    root = _voices_root(voices_dir)
    sanitized = _sanitize_voice_id(voice_id)
    directory = _voice_dir(root, sanitized)

    audio_path = directory / AUDIO_FILENAME
    ref_text_path = directory / REF_TEXT_FILENAME

    if not directory.is_dir():
        raise FileNotFoundError(f"Voice {sanitized!r} not found under {root}")
    if not audio_path.is_file():
        raise FileNotFoundError(f"Voice {sanitized!r} missing {AUDIO_FILENAME} in {directory}")
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
    """Load ref_text.txt from the same voice folder as audio.wav, if present."""
    path = Path(audio_path)
    try:
        resolved = path.resolve()
    except OSError:
        return None

    if resolved.name != AUDIO_FILENAME:
        return None

    ref_text_path = resolved.parent / REF_TEXT_FILENAME
    if not ref_text_path.is_file():
        return None

    ref_text = ref_text_path.read_text(encoding="utf-8").strip()
    return ref_text or None
