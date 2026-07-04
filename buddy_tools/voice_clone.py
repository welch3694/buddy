"""Precompute and reuse Qwen3-TTS voice clone prompts for stable timbre."""

from __future__ import annotations

import logging
from typing import Any

from buddy_tools.tool_logging import log_tool_failure

logger = logging.getLogger(__name__)

# Match faster-qwen3-tts default append_silence behavior for ICL mode.
APPEND_SILENCE_SECONDS = 0.5


def precompute_voice_clone_prompt(handler: Any) -> Any | None:
    """Build a reusable voice_clone_prompt from the handler's current ref audio/text."""
    ref_audio = getattr(handler, "ref_audio", None)
    ref_text = getattr(handler, "ref_text", None)
    if ref_audio is None or not ref_text:
        log_tool_failure(
            "voice_clone",
            "skipped: missing ref_audio or ref_text",
            context={"ref_audio": str(ref_audio) if ref_audio else None},
        )
        return None

    backend = getattr(handler, "backend", None)
    if backend != "faster_qwen3_tts":
        log_tool_failure(
            "voice_clone",
            f"skipped: unsupported backend {backend!r}",
            context={"ref_audio": str(ref_audio)},
        )
        return None

    model_wrapper = getattr(handler, "model", None)
    if model_wrapper is None or not hasattr(model_wrapper, "model"):
        log_tool_failure(
            "voice_clone",
            "skipped: TTS model not loaded",
            context={"ref_audio": str(ref_audio)},
        )
        return None

    xvec_only = bool(getattr(handler, "xvec_only", False))
    try:
        if xvec_only:
            prompt_items = model_wrapper.model.create_voice_clone_prompt(
                ref_audio=str(ref_audio),
                ref_text="",
                x_vector_only_mode=True,
            )
        else:
            ref_audio_input = model_wrapper._load_ref_audio_with_silence(
                ref_audio,
                silence_secs=APPEND_SILENCE_SECONDS,
            )
            prompt_items = model_wrapper.model.create_voice_clone_prompt(
                ref_audio=ref_audio_input,
                ref_text=ref_text,
            )
    except Exception as exc:
        log_tool_failure(
            "voice_clone",
            f"failed to precompute voice clone prompt for {ref_audio!r}",
            exc=exc,
            context={"ref_audio": str(ref_audio)},
        )
        return None

    return prompt_items


def refresh_voice_clone_prompt(handler: Any) -> bool:
    """Recompute and store voice_clone_prompt on the TTS handler."""
    prompt = precompute_voice_clone_prompt(handler)
    handler.voice_clone_prompt = prompt
    if prompt is None:
        return False

    ref_text = getattr(handler, "ref_text", "") or ""
    logger.info(
        "Precomputed voice clone prompt for ref_audio=%r ref_text=%r",
        str(getattr(handler, "ref_audio", "")),
        ref_text[:80] + ("..." if len(ref_text) > 80 else ""),
    )
    return True


def voice_clone_log_context(handler: Any) -> str:
    """Short summary of the active clone inputs for per-utterance logging."""
    ref_audio = getattr(handler, "ref_audio", None)
    ref_text = getattr(handler, "ref_text", None) or ""
    cached = getattr(handler, "voice_clone_prompt", None) is not None
    return (
        f"ref_audio={ref_audio!s} ref_text={ref_text[:80]!r}"
        f"{'...' if len(ref_text) > 80 else ''} cached_prompt={cached}"
    )
