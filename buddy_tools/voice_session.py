"""Runtime voice switching: keep Qwen3 ref_audio and ref_text in sync."""

from __future__ import annotations

import logging
from typing import Any

from speech_to_speech.api.openai_realtime.runtime_config import RuntimeConfig

from buddy_tools.voices import VoiceProfile, get_voice

logger = logging.getLogger(__name__)

_tts_handler: Any | None = None


def get_tts_handler() -> Any | None:
    return _tts_handler


def set_tts_handler(handler: Any | None) -> None:
    global _tts_handler
    _tts_handler = handler


def register_pipeline_handlers(handlers: list[Any]) -> None:
    """Capture the Qwen3 TTS handler instance from the live pipeline."""
    for handler in handlers:
        if handler.__class__.__name__ == "Qwen3TTSHandler":
            set_tts_handler(handler)
            logger.debug("Registered Qwen3TTSHandler for runtime voice switching")
            return


def apply_voice(
    voice_id: str,
    *,
    runtime_config: RuntimeConfig | None = None,
    tts_handler: Any | None = None,
) -> VoiceProfile:
    """Apply a named voice to runtime config and the TTS handler atomically."""
    profile = get_voice(voice_id)
    audio_path = profile.audio_path
    audio_value = str(audio_path)
    handler = tts_handler if tts_handler is not None else get_tts_handler()

    if handler is not None:
        handler.ref_audio = audio_path
        handler.ref_text = profile.ref_text
        logger.info("Applied voice %r to TTS handler", profile.id)

    if runtime_config is not None:
        session = runtime_config.session
        if session.audio is None or session.audio.output is None:
            raise ValueError("runtime_config.session.audio.output is not initialized")
        session.audio.output.voice = audio_value
        logger.info("Applied voice %r to runtime session", profile.id)

    if handler is None and runtime_config is None:
        raise ValueError("apply_voice requires runtime_config and/or an available TTS handler")

    return profile
