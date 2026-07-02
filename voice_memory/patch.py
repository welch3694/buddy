"""Monkey-patch speech-to-speech to add local memory tool execution."""

from __future__ import annotations

from typing import Any

from voice_memory.bootstrap import insert_memory_tool_executor


def apply_memory_patches() -> None:
    import speech_to_speech.s2s_pipeline as pipeline

    if getattr(pipeline, "_memory_patches_applied", False):
        return

    original_build = pipeline._build_pipeline_handlers

    def patched_build_pipeline_handlers(*args: Any, **kwargs: Any) -> list[Any]:
        handlers = original_build(*args, **kwargs)
        return insert_memory_tool_executor(
            handlers,
            stop_event=kwargs["stop_event"],
            text_prompt_queue=kwargs["text_prompt_queue"],
            lm_response_queue=kwargs["lm_response_queue"],
            transcription_notifier_setup=kwargs["transcription_notifier_setup"],
            speculative_turns=kwargs.get("speculative_turns"),
        )

    pipeline._build_pipeline_handlers = patched_build_pipeline_handlers  # type: ignore[method-assign]
    pipeline._memory_patches_applied = True
