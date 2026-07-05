"""Monkey-patch speech-to-speech to add local tool execution."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

from buddy_tools.infra.bootstrap import insert_local_tool_executor
from buddy_tools.infra.context_budget import (
    ContextBudget,
    build_overflow_apology_text,
    is_context_overflow_error,
    preflight_trim,
    recover_after_overflow,
)
from buddy_tools.voice.listening_pause import configure_listening_pause, process_transcription_with_listening_pause
from buddy_tools.voice.clone import refresh_voice_clone_prompt, voice_clone_log_context
from buddy_tools.voice.voices import ref_text_for_audio_path

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
        previous_ref_audio = self.ref_audio
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
        if self.ref_audio != previous_ref_audio:
            self.voice_clone_prompt = None
            refresh_voice_clone_prompt(self)
        logger.debug("Synced Qwen3-TTS ref_text from voice folder for %r", self.ref_audio)

    Qwen3TTSHandler._apply_session_voice_override = _apply_session_voice_override_with_ref_text  # type: ignore[method-assign]
    Qwen3TTSHandler._buddy_ref_text_patch_applied = True


def _patch_qwen3_voice_clone_stability() -> None:
    from speech_to_speech.TTS.qwen3_tts_handler import Qwen3TTSHandler

    if getattr(Qwen3TTSHandler, "_buddy_voice_clone_patch_applied", False):
        return

    original_warmup = Qwen3TTSHandler.warmup
    original_process_voice_clone = Qwen3TTSHandler._process_voice_clone

    def warmup_with_prompt_cache(self: Any) -> None:
        original_warmup(self)
        refresh_voice_clone_prompt(self)

    def _process_voice_clone_with_cached_prompt(self: Any, text: str) -> Iterator[Any]:
        if self.ref_audio is not None:
            if getattr(self, "voice_clone_prompt", None) is None:
                refresh_voice_clone_prompt(self)
            logger.info("Qwen3-TTS synthesizing with %s", voice_clone_log_context(self))

        if self.backend == "mlx":
            yield from original_process_voice_clone(self, text)
            return

        utterance_max_new_tokens = self._estimate_max_new_tokens(text)
        voice_clone_prompt = getattr(self, "voice_clone_prompt", None)
        yield from self._stream(
            self.model.generate_voice_clone_streaming(
                text=text,
                language=self.language,
                ref_audio=self.ref_audio,
                ref_text=self.ref_text,
                xvec_only=self.xvec_only,
                chunk_size=self.streaming_chunk_size,
                max_new_tokens=utterance_max_new_tokens,
                parity_mode=self.parity_mode,
                non_streaming_mode=self.non_streaming_mode,
                voice_clone_prompt=voice_clone_prompt,
            ),
            label="voice_clone_parity" if self.parity_mode else "voice_clone",
        )

    Qwen3TTSHandler.warmup = warmup_with_prompt_cache  # type: ignore[method-assign]
    Qwen3TTSHandler._process_voice_clone = _process_voice_clone_with_cached_prompt  # type: ignore[method-assign]
    Qwen3TTSHandler._buddy_voice_clone_patch_applied = True


def _patch_pocket_tts_voice_logging() -> None:
    from speech_to_speech.TTS.pocket_tts_handler import PocketTTSHandler
    from speech_to_speech.pipeline.messages import EndOfResponse

    if getattr(PocketTTSHandler, "_buddy_voice_log_patch_applied", False):
        return

    original_process = PocketTTSHandler.process

    def process_with_voice_log(self: Any, tts_input: Any) -> Iterator[Any]:
        if not isinstance(tts_input, EndOfResponse):
            voice = getattr(self, "voice", None)
            if voice is not None:
                logger.info("Pocket TTS synthesizing with voice=%r", voice)
        yield from original_process(self, tts_input)

    PocketTTSHandler.process = process_with_voice_log  # type: ignore[method-assign]
    PocketTTSHandler._buddy_voice_log_patch_applied = True


def _patch_transcription_notifier_listening_pause() -> None:
    from speech_to_speech.STT.transcription_notifier import TranscriptionNotifier

    if getattr(TranscriptionNotifier, "_buddy_listening_pause_patch_applied", False):
        return

    original_process = TranscriptionNotifier.process

    def process_with_listening_pause(self: Any, transcription: Any) -> Iterator[Any]:
        gated = process_transcription_with_listening_pause(self, transcription)
        if gated is not None:
            yield from gated
            return
        yield from original_process(self, transcription)

    TranscriptionNotifier.process = process_with_listening_pause  # type: ignore[method-assign]
    TranscriptionNotifier._buddy_listening_pause_patch_applied = True


def _iter_llm_outputs_with_context_budget(
    original_process: Any,
    handler: Any,
    request: Any,
) -> Iterator[Any]:
    """Run preflight trim and intercept context-overflow failures (testable helper)."""
    from speech_to_speech.pipeline.messages import EndOfResponse, LLMResponseChunk

    runtime_config = request.runtime_config
    response = request.response
    instructions = (
        response.instructions if response and response.instructions else runtime_config.session.instructions
    ) or ""
    tools = response.tools if response and response.tools else runtime_config.session.tools
    budget = ContextBudget.from_env()

    try:
        preflight_trim(runtime_config.chat, instructions, tools, budget)
    except Exception:
        logger.exception("Context preflight wrapper failed; continuing with generation")

    for item in original_process(handler, request):
        if isinstance(item, EndOfResponse) and item.error and is_context_overflow_error(item.error):
            try:
                recover_after_overflow(runtime_config.chat, instructions, tools, budget)
            except Exception:
                logger.exception("Context overflow recovery wrapper failed")
            if handler._turn_output_allowed(item.turn_id, item.turn_revision):
                yield LLMResponseChunk(
                    text=build_overflow_apology_text(),
                    language_code=request.language_code,
                    runtime_config=runtime_config,
                    response=response,
                    turn_id=item.turn_id,
                    turn_revision=item.turn_revision,
                    speech_stopped_at_s=request.speech_stopped_at_s,
                    cancel_generation=item.cancel_generation,
                )
            yield EndOfResponse(
                turn_id=item.turn_id,
                turn_revision=item.turn_revision,
                cancel_generation=item.cancel_generation,
            )
            continue
        yield item


def _patch_llm_context_budget() -> None:
    from speech_to_speech.LLM.base_openai_compatible_language_model import BaseOpenAICompatibleHandler

    if getattr(BaseOpenAICompatibleHandler, "_buddy_context_budget_patch_applied", False):
        return

    original_process = BaseOpenAICompatibleHandler.process

    def process_with_context_budget(self: Any, request: Any) -> Iterator[Any]:
        yield from _iter_llm_outputs_with_context_budget(original_process, self, request)

    BaseOpenAICompatibleHandler.process = process_with_context_budget  # type: ignore[method-assign]
    BaseOpenAICompatibleHandler._buddy_context_budget_patch_applied = True


def _configure_listening_pause_from_handlers(
    handlers: list[Any],
    *,
    should_listen: Any | None,
) -> None:
    cancel_scope = None
    for handler in handlers:
        handler_cancel_scope = getattr(handler, "cancel_scope", None)
        if handler_cancel_scope is not None:
            cancel_scope = handler_cancel_scope
            break

    configure_listening_pause(
        cancel_scope=cancel_scope,
        should_listen=should_listen,
    )


def apply_patches() -> None:
    import speech_to_speech.s2s_pipeline as pipeline

    if getattr(pipeline, "_buddy_tools_patches_applied", False):
        return

    from buddy_tools.infra.data_dir import configure_user_data

    configure_user_data()

    _patch_qwen3_ref_text_sync()
    _patch_qwen3_voice_clone_stability()
    _patch_pocket_tts_voice_logging()
    _patch_transcription_notifier_listening_pause()
    _patch_llm_context_budget()

    original_build = pipeline._build_pipeline_handlers

    def patched_build_pipeline_handlers(*args: Any, **kwargs: Any) -> list[Any]:
        handlers = original_build(*args, **kwargs)
        _configure_listening_pause_from_handlers(
            handlers,
            should_listen=kwargs.get("should_listen"),
        )
        return insert_local_tool_executor(
            handlers,
            stop_event=kwargs["stop_event"],
            text_prompt_queue=kwargs["text_prompt_queue"],
            lm_response_queue=kwargs["lm_response_queue"],
            transcription_notifier_setup=kwargs["transcription_notifier_setup"],
            speculative_turns=kwargs.get("speculative_turns"),
            should_listen=kwargs.get("should_listen"),
        )

    pipeline._build_pipeline_handlers = patched_build_pipeline_handlers  # type: ignore[method-assign]
    pipeline._buddy_tools_patches_applied = True
