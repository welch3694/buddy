"""Monkey-patch speech-to-speech to add local tool execution."""

from __future__ import annotations

import logging
from typing import Any

from buddy_tools.bootstrap import insert_local_tool_executor
from buddy_tools.voices import ref_text_for_audio_path

logger = logging.getLogger(__name__)


def _patch_qwen3_ref_text_sync() -> None:
    from speech_to_speech.TTS.qwen3_tts_handler import Qwen3TTSHandler

    if getattr(Qwen3TTSHandler, "_buddy_ref_text_patch_applied", False):
        return

    original_apply = Qwen3TTSHandler._apply_session_voice_override

    def _apply_session_voice_override_with_ref_text(
        self: Any,
        model_type: str,
        runtime_config: Any | None = None,
        response: Any | None = None,
    ) -> None:
        original_apply(self, model_type, runtime_config, response)
        if self.ref_audio is None:
            return

        ref_text = ref_text_for_audio_path(self.ref_audio)
        if ref_text is None:
            logger.warning(
                "Qwen3-TTS voice switched to %r but no %s was found alongside it",
                self.ref_audio,
                "ref_text.txt",
            )
            return

        self.ref_text = ref_text
        logger.debug("Synced Qwen3-TTS ref_text from voice folder for %r", self.ref_audio)

    Qwen3TTSHandler._apply_session_voice_override = _apply_session_voice_override_with_ref_text  # type: ignore[method-assign]
    Qwen3TTSHandler._buddy_ref_text_patch_applied = True


def apply_patches() -> None:
    import speech_to_speech.s2s_pipeline as pipeline

    if getattr(pipeline, "_buddy_tools_patches_applied", False):
        return

    _patch_qwen3_ref_text_sync()

    original_build = pipeline._build_pipeline_handlers

    def patched_build_pipeline_handlers(*args: Any, **kwargs: Any) -> list[Any]:
        handlers = original_build(*args, **kwargs)
        return insert_local_tool_executor(
            handlers,
            stop_event=kwargs["stop_event"],
            text_prompt_queue=kwargs["text_prompt_queue"],
            lm_response_queue=kwargs["lm_response_queue"],
            transcription_notifier_setup=kwargs["transcription_notifier_setup"],
            speculative_turns=kwargs.get("speculative_turns"),
        )

    pipeline._build_pipeline_handlers = patched_build_pipeline_handlers  # type: ignore[method-assign]
    pipeline._buddy_tools_patches_applied = True
