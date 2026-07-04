"""Runtime voice switching for Qwen3 and Pocket TTS handlers."""

from __future__ import annotations

import logging
from typing import Any

from speech_to_speech.api.openai_realtime.runtime_config import RuntimeConfig

from buddy_tools.personality import get_active_personality
from buddy_tools.tool_logging import log_tool_failure
from buddy_tools.voice_clone import refresh_voice_clone_prompt
from buddy_tools.voices import VoiceProfile, get_voice

logger = logging.getLogger(__name__)

_tts_handler: Any | None = None

SUPPORTED_TTS_HANDLERS = frozenset({"Qwen3TTSHandler", "PocketTTSHandler"})


def get_tts_handler() -> Any | None:
    return _tts_handler


def set_tts_handler(handler: Any | None) -> None:
    global _tts_handler
    _tts_handler = handler


def register_pipeline_handlers(handlers: list[Any]) -> None:
    """Capture the active TTS handler instance from the live pipeline."""
    for handler in handlers:
        class_name = handler.__class__.__name__
        if class_name in SUPPORTED_TTS_HANDLERS:
            set_tts_handler(handler)
            logger.debug("Registered %s for runtime voice switching", class_name)
            return


def _apply_voice_to_handler(handler: Any, profile: VoiceProfile) -> None:
    """Apply a voice profile to the live TTS handler."""
    class_name = handler.__class__.__name__
    audio_path = profile.audio_path
    audio_value = str(audio_path)

    if class_name == "Qwen3TTSHandler":
        handler.ref_audio = audio_path
        handler.ref_text = profile.ref_text
        handler.voice_clone_prompt = None
        refresh_voice_clone_prompt(handler)
        logger.info("Applied voice %r to Qwen3 TTS handler", profile.id)
        return

    if class_name == "PocketTTSHandler":
        handler.voice = audio_value
        model = getattr(handler, "model", None)
        if model is not None:
            handler.voice_state = model.get_state_for_audio_prompt(audio_value)
            logger.info("Applied voice %r to Pocket TTS handler", profile.id)
        else:
            logger.info(
                "Queued voice %r for Pocket TTS handler (model not loaded yet)",
                profile.id,
            )
        return

    logger.warning(
        "TTS handler %s is not supported for runtime voice switching; updated session only",
        class_name,
    )


def apply_voice(
    voice_id: str,
    *,
    runtime_config: RuntimeConfig | None = None,
    tts_handler: Any | None = None,
) -> VoiceProfile:
    """Apply a named voice to runtime config and the TTS handler atomically."""
    profile = get_voice(voice_id)
    audio_value = str(profile.audio_path)
    handler = tts_handler if tts_handler is not None else get_tts_handler()

    if handler is not None:
        _apply_voice_to_handler(handler, profile)

    if runtime_config is not None:
        session = runtime_config.session
        if session.audio is None or session.audio.output is None:
            log_tool_failure(
                "apply_voice",
                "runtime_config.session.audio.output is not initialized",
                context={"voice_id": voice_id},
            )
            raise ValueError("runtime_config.session.audio.output is not initialized")
        session.audio.output.voice = audio_value
        logger.info("Applied voice %r to runtime session", profile.id)

    if handler is None and runtime_config is None:
        log_tool_failure(
            "apply_voice",
            "requires runtime_config and/or an available TTS handler",
            context={"voice_id": voice_id},
        )
        raise ValueError("apply_voice requires runtime_config and/or an available TTS handler")

    return profile


def apply_startup_voice(*, runtime_config: RuntimeConfig | None = None) -> VoiceProfile | None:
    """Wire the active personality's voice into session config and the TTS handler."""
    if runtime_config is None:
        return None

    profile = get_active_personality()
    return apply_voice(profile.voice_id, runtime_config=runtime_config, tts_handler=get_tts_handler())
