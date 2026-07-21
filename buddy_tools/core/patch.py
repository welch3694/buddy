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
from buddy_tools.voice.turn_state import VoiceTurnState, set_turn_state
from buddy_tools.voice.voices import ref_text_for_audio_path

logger = logging.getLogger(__name__)


def _ensure_speculative_turns(kwargs: dict[str, Any]) -> Any:
    """Local pipeline mode omits speculative_turns; create and inject one for endpointing."""
    from speech_to_speech.pipeline.speculative_turns import SpeculativeTurnTracker

    speculative_turns = kwargs.get("speculative_turns")
    if speculative_turns is None:
        speculative_turns = SpeculativeTurnTracker()
        kwargs["speculative_turns"] = speculative_turns
        logger.info("Created SpeculativeTurnTracker for local pipeline (speech-to-speech local mode omits it)")

    vad_handler_kwargs = kwargs.get("vad_handler_kwargs")
    if vad_handler_kwargs is not None:
        vars(vad_handler_kwargs)["speculative_turns"] = speculative_turns

    for kw_name in ("language_model_handler_kwargs", "responses_api_language_model_handler_kwargs"):
        lm_kwargs = kwargs.get(kw_name)
        if lm_kwargs is not None:
            vars(lm_kwargs)["speculative_turns"] = speculative_turns

    return speculative_turns


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
        set_turn_state(
            VoiceTurnState.SPEAKING,
            reason="tts_start",
            announce_ui=True,
        )

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
            set_turn_state(
                VoiceTurnState.SPEAKING,
                reason="tts_start",
                turn_id=getattr(tts_input, "turn_id", None),
                turn_revision=getattr(tts_input, "turn_revision", None),
                announce_ui=True,
            )
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


def _log_offered_tools(request: Any, tools: Any, tool_choice: Any) -> None:
    """Log tools offered to the model this turn (distinct from returned tool calls)."""
    count = len(tools) if tools else 0
    choice_repr = tool_choice
    if tool_choice is not None and not isinstance(tool_choice, (str, dict)):
        try:
            choice_repr = tool_choice.model_dump(exclude_none=True)
        except Exception:
            choice_repr = repr(tool_choice)
    logger.info(
        "Offered tools: count=%d tool_choice=%r turn=%s rev=%s",
        count,
        choice_repr,
        getattr(request, "turn_id", None),
        getattr(request, "turn_revision", None),
    )


# Appended after s2s voice rules so Buddy anti-fabrication wins over "just speak".
_BUDDY_VOICE_PROMPT_OVERRIDE = """\
## Buddy Tool Rules
- Never claim you started a skill, saved or remembered something, cancelled a skill, or updated config until a tool result confirms it.
- If the user asked for an action that needs a tool, call the tool. Do not narrate completion without calling it.
- If unsure whether a tool is needed for an action request, call the tool or ask to confirm — do not just speak a success claim.\
"""


def _iter_llm_outputs_with_context_budget(
    original_process: Any,
    handler: Any,
    request: Any,
) -> Iterator[Any]:
    """Run preflight trim and intercept context-overflow failures (testable helper)."""
    from speech_to_speech.pipeline.messages import EndOfResponse, LLMResponseChunk

    set_turn_state(
        VoiceTurnState.GENERATING,
        reason="llm_start",
        turn_id=getattr(request, "turn_id", None),
        turn_revision=getattr(request, "turn_revision", None),
        announce_ui=True,
    )

    runtime_config = request.runtime_config
    response = request.response
    instructions = (
        response.instructions if response and response.instructions else runtime_config.session.instructions
    ) or ""
    tools = response.tools if response and response.tools else runtime_config.session.tools
    tool_choice = (
        response.tool_choice if response and response.tool_choice else runtime_config.session.tool_choice
    )
    _log_offered_tools(request, tools, tool_choice)
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


def _mark_listening_after_response() -> None:
    from buddy_tools.voice.listening_pause import get_listening_pause_controller

    if get_listening_pause_controller().paused:
        return
    set_turn_state(VoiceTurnState.LISTENING, reason="response_complete", announce_ui=True)


def _wrap_should_listen_for_turn_state(should_listen: Any) -> None:
    """Wrap Event.set so playback-complete re-enable marks listening (once)."""
    if should_listen is None or getattr(should_listen, "_buddy_turn_state_wrapped", False):
        return

    original_set = should_listen.set

    def set_and_mark_listening() -> None:
        original_set()
        try:
            from buddy_tools.voice.turn_state import current_turn_state

            current = current_turn_state()
        except Exception:
            return
        if current in (VoiceTurnState.SPEAKING, VoiceTurnState.GENERATING):
            _mark_listening_after_response()

    should_listen.set = set_and_mark_listening  # type: ignore[method-assign]
    should_listen._buddy_turn_state_wrapped = True  # type: ignore[attr-defined]


def _patch_response_complete_turn_state() -> None:
    """Return to listening when playback finishes and should_listen is re-enabled.

    Also replaces ``LocalAudioStreamer.run`` with a reload-capable duplex loop so
    Windows default-mic changes are picked up without a process restart (#155).
    """
    from speech_to_speech.connections.local_audio_streamer import LocalAudioStreamer

    if not getattr(LocalAudioStreamer, "_buddy_turn_state_patch_applied", False):
        def run_with_turn_state(self: Any) -> None:
            _wrap_should_listen_for_turn_state(self.should_listen)
            try:
                from buddy_tools.companion.playback_progress import (
                    install_playback_progress_tracking,
                )

                install_playback_progress_tracking(self)
            except Exception:
                logger.exception("Failed to install companion playback progress tracking")
            from buddy_tools.voice.microphone import run_local_audio_with_reload

            run_local_audio_with_reload(self)

        LocalAudioStreamer.run = run_with_turn_state  # type: ignore[method-assign]
        LocalAudioStreamer._buddy_turn_state_patch_applied = True

    try:
        from speech_to_speech.connections.websocket_streamer import WebSocketStreamer
    except Exception:
        return

    if getattr(WebSocketStreamer, "_buddy_turn_state_patch_applied", False):
        return

    original_ws_init = WebSocketStreamer.__init__

    def init_with_turn_state(self: Any, *args: Any, **kwargs: Any) -> None:
        original_ws_init(self, *args, **kwargs)
        _wrap_should_listen_for_turn_state(self.should_listen)

    WebSocketStreamer.__init__ = init_with_turn_state  # type: ignore[method-assign]
    WebSocketStreamer._buddy_turn_state_patch_applied = True


def _patch_llm_context_budget() -> None:
    from speech_to_speech.LLM.base_openai_compatible_language_model import BaseOpenAICompatibleHandler

    if getattr(BaseOpenAICompatibleHandler, "_buddy_context_budget_patch_applied", False):
        return

    original_process = BaseOpenAICompatibleHandler.process

    def process_with_context_budget(self: Any, request: Any) -> Iterator[Any]:
        yield from _iter_llm_outputs_with_context_budget(original_process, self, request)

    BaseOpenAICompatibleHandler.process = process_with_context_budget  # type: ignore[method-assign]
    BaseOpenAICompatibleHandler._buddy_context_budget_patch_applied = True


def _patch_voice_prompt_anti_fabrication() -> None:
    """Append Buddy tool rules after s2s voice tail so anti-fabrication wins over 'just speak'."""
    import speech_to_speech.LLM.voice_prompt as voice_prompt

    if getattr(voice_prompt, "_buddy_anti_fabrication_patch_applied", False):
        return

    original_build = voice_prompt.build_voice_system_prompt

    def build_with_buddy_tool_rules(session_prompt: str, *, tool_section: str = "") -> str:
        base = original_build(session_prompt, tool_section=tool_section)
        return f"{base.rstrip()}\n\n{_BUDDY_VOICE_PROMPT_OVERRIDE.strip()}"

    voice_prompt.build_voice_system_prompt = build_with_buddy_tool_rules  # type: ignore[assignment]
    voice_prompt._buddy_anti_fabrication_patch_applied = True


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


def _patch_graceful_shutdown() -> None:
    """Fix Ctrl+C on Windows and ensure pipeline threads can exit."""
    from speech_to_speech.utils.thread_manager import ThreadManager

    if getattr(ThreadManager, "_buddy_shutdown_patch_applied", False):
        return

    import speech_to_speech.s2s_pipeline as pipeline
    from speech_to_speech.pipeline.messages import PIPELINE_END

    original_build_pipeline = pipeline.build_pipeline
    original_stop = ThreadManager.stop

    def patched_build_pipeline(*args: Any, **kwargs: Any) -> ThreadManager:
        manager = original_build_pipeline(*args, **kwargs)
        queues = kwargs.get("queues_and_events")
        if queues is None and args:
            queues = args[-1]
        if isinstance(queues, dict):
            stop_event = queues.get("stop_event")
            if stop_event is not None:
                for handler in manager.handlers:
                    if handler.__class__.__name__ == "LocalAudioStreamer":
                        handler.stop_event = stop_event
        _wire_pulse_cancel_scope_from_handlers(manager.handlers)
        return manager

    def stop_with_unblock(self: ThreadManager) -> None:
        for handler in self.handlers:
            handler.stop_event.set()
            queue_in = getattr(handler, "queue_in", None)
            if queue_in is not None:
                try:
                    queue_in.put_nowait(PIPELINE_END)
                except Exception:
                    pass

        for i, thread in enumerate(self.threads):
            if thread.is_alive():
                thread.join(timeout=5.0)
                if thread.is_alive():
                    logger.warning(
                        "Thread %d (%s) did not terminate within timeout",
                        i,
                        thread.name,
                    )

    def wait_with_signal_processing(self: ThreadManager) -> None:
        """Join worker threads with timeouts so SIGINT can run on the main thread."""
        while True:
            alive = [thread for thread in self.threads if thread.is_alive()]
            if not alive:
                break
            for thread in alive:
                thread.join(timeout=0.2)

    pipeline.build_pipeline = patched_build_pipeline  # type: ignore[assignment]
    ThreadManager.wait = wait_with_signal_processing  # type: ignore[method-assign]
    ThreadManager.stop = stop_with_unblock  # type: ignore[method-assign]
    ThreadManager._buddy_shutdown_patch_applied = True


def _wire_pulse_cancel_scope_from_handlers(handlers: list[Any]) -> None:
    """Use an existing CancelScope if the pipeline already has one (e.g. realtime).

    Do **not** invent and assign a new CancelScope onto local-mode LLM/TTS handlers —
    that path previously left TTS stuck after ``tts_start`` with no audible output.
    """
    from buddy_tools.pulse.inject import set_pulse_audio_out_queue, set_pulse_cancel_scope

    scope = None
    for handler in handlers:
        existing = getattr(handler, "cancel_scope", None)
        if existing is not None:
            scope = existing
            break
    set_pulse_cancel_scope(scope)

    for handler in handlers:
        if handler.__class__.__name__ == "LocalAudioStreamer":
            set_pulse_audio_out_queue(getattr(handler, "output_queue", None))
            break


def _wire_vad_speech_activity_from_handlers(handlers: list[Any]) -> None:
    """Local mode leaves text_output_queue=None, so VAD logs speech start but never emits events.

    Attach a queue to the live VADHandler and observe SpeechStarted/Stopped for pulse gates.
    """
    from queue import Queue

    from buddy_tools.pulse.gates import install_speech_activity_queue_observer

    for handler in handlers:
        if handler.__class__.__name__ != "VADHandler":
            continue
        queue = getattr(handler, "text_output_queue", None)
        if queue is None:
            queue = Queue()
            handler.text_output_queue = queue
            logger.info(
                "Attached text_output_queue to VADHandler for speech-activity pulse gates"
            )
        install_speech_activity_queue_observer(queue)
        return


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
    _patch_voice_prompt_anti_fabrication()
    _patch_response_complete_turn_state()
    _patch_graceful_shutdown()

    original_build = pipeline._build_pipeline_handlers

    def patched_build_pipeline_handlers(*args: Any, **kwargs: Any) -> list[Any]:
        speculative_turns = _ensure_speculative_turns(kwargs)
        handlers = original_build(*args, **kwargs)
        # LocalAudioStreamer is a comms handler — only available after full build_pipeline.
        # Cancel scope / audio drain are wired there; VAD speech activity is wired here.
        _wire_vad_speech_activity_from_handlers(handlers)
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
            speculative_turns=speculative_turns,
            should_listen=kwargs.get("should_listen"),
        )

    pipeline._build_pipeline_handlers = patched_build_pipeline_handlers  # type: ignore[method-assign]
    pipeline._buddy_tools_patches_applied = True
