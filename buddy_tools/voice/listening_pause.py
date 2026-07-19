"""Voice commands to pause and resume Buddy's responsiveness."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from threading import Event
from typing import Any

from speech_to_speech.pipeline.cancel_scope import CancelScope
from speech_to_speech.pipeline.events import PartialTranscriptionEvent, TranscriptionCompletedEvent
from speech_to_speech.pipeline.messages import PartialTranscription, Transcription

from buddy_tools.voice.turn_state import VoiceTurnState, configure_turn_state, set_turn_state

logger = logging.getLogger(__name__)

PAUSED_PARTIAL_PREFIX = "[paused - ignored] "
STOP_LISTENING_PHRASE = "stop listening"
START_LISTENING_PHRASE = "start listening"


def build_listening_pause_instructions() -> str:
    """Session instructions so the agent can explain pause/resume voice commands."""
    return (
        "Voice listening controls: The user can pause and resume your responsiveness with exact "
        f'voice commands. Saying exactly "{STOP_LISTENING_PHRASE}" puts you in a paused mode where '
        f'you ignore all other speech until they say exactly "{START_LISTENING_PHRASE}" again. '
        "While paused, you do not respond, call tools, or speak. When the user asks what you can do, "
        "how to pause you, avoid triggering on background conversation, or similar capability "
        f'questions, explain that they can use these exact phrases: "{STOP_LISTENING_PHRASE}" to '
        f'pause and "{START_LISTENING_PHRASE}" to resume.'
    )


def normalize_transcript(text: str) -> str:
    """Lowercase transcript and strip punctuation for phrase matching."""
    normalized = text.lower().strip()
    normalized = re.sub(r"[^\w\s]", "", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized


def matches_stop_listening(text: str) -> bool:
    """Match only an exact stop-listening command."""
    return normalize_transcript(text) == STOP_LISTENING_PHRASE


def matches_start_listening(text: str) -> bool:
    """Match only an exact start-listening command."""
    return normalize_transcript(text) == START_LISTENING_PHRASE


@dataclass
class ListeningPauseController:
    """Tracks whether Buddy should ignore non-resume speech."""

    cancel_scope: CancelScope | None = None
    should_listen: Event | None = None
    paused: bool = field(default=False, init=False)

    def pause(self) -> None:
        if self.paused:
            return
        self.paused = True
        set_turn_state(VoiceTurnState.PAUSED, reason="stop_listening", announce_ui=True)
        if self.cancel_scope is not None:
            self.cancel_scope.cancel()
        if self.should_listen is not None:
            self.should_listen.set()

    def resume(self) -> bool:
        """Resume listening. Returns True when state changed."""
        if not self.paused:
            return False
        self.paused = False
        set_turn_state(VoiceTurnState.LISTENING, reason="start_listening", announce_ui=True)
        if self.should_listen is not None:
            self.should_listen.set()
        return True


def _reenable_listen(notifier: Any, controller: ListeningPauseController | None = None) -> None:
    """VAD clears should_listen when a speech segment ends; restore it when we skip LLM/TTS."""
    active_controller = controller or get_listening_pause_controller()
    listen_event = notifier.should_listen or active_controller.should_listen
    if listen_event is not None:
        listen_event.set()
        logger.debug("Listening re-enabled after gated transcription")


_controller = ListeningPauseController()


def get_listening_pause_controller() -> ListeningPauseController:
    return _controller


def configure_listening_pause(
    *,
    cancel_scope: CancelScope | None = None,
    should_listen: Event | None = None,
) -> ListeningPauseController:
    controller = get_listening_pause_controller()
    if cancel_scope is not None:
        controller.cancel_scope = cancel_scope
    if should_listen is not None:
        controller.should_listen = should_listen
    return controller


def _extract_transcription_fields(transcription: Any) -> tuple[str, str | None, str | None, int | None, float | None]:
    if isinstance(transcription, Transcription):
        return (
            str(transcription.text),
            transcription.language_code,
            transcription.turn_id,
            transcription.turn_revision,
            transcription.speech_stopped_at_s,
        )
    return str(transcription), None, None, None, None


def process_transcription_with_listening_pause(
    notifier: Any,
    transcription: Any,
    *,
    controller: ListeningPauseController | None = None,
) -> Any:
    """Gate STT output before it reaches chat history or the LLM."""
    active_controller = controller or get_listening_pause_controller()

    if notifier.text_output_queue is not None:
        configure_turn_state(text_output_queue=notifier.text_output_queue)

    if isinstance(transcription, PartialTranscription):
        if active_controller.paused and transcription.text:
            if notifier.text_output_queue is not None:
                notifier.text_output_queue.put(
                    PartialTranscriptionEvent(
                        delta=f"{PAUSED_PARTIAL_PREFIX}{transcription.text}",
                        turn_id=transcription.turn_id,
                        turn_revision=transcription.turn_revision,
                    )
                )
                logger.debug(
                    "Partial transcription while paused: %s",
                    str(transcription.text)[:80],
                )
            return iter(())
        if transcription.text and not active_controller.paused:
            set_turn_state(
                VoiceTurnState.LISTENING,
                reason="partial_transcription",
                turn_id=transcription.turn_id,
                turn_revision=transcription.turn_revision,
            )
        return None

    transcript, language_code, turn_id, turn_revision, speech_stopped_at_s = _extract_transcription_fields(
        transcription
    )

    if notifier.text_output_queue is not None:
        completed_transcript = transcript
        if active_controller.paused and transcript:
            completed_transcript = f"{PAUSED_PARTIAL_PREFIX}{transcript}"
        notifier.text_output_queue.put(
            TranscriptionCompletedEvent(
                transcript=completed_transcript,
                language_code=language_code,
                turn_id=turn_id,
                turn_revision=turn_revision,
                speech_stopped_at_s=speech_stopped_at_s,
            )
        )

    if not transcript:
        logger.debug("Transcription completed with empty transcript")
        _reenable_listen(notifier, active_controller)
        return iter(())

    if matches_stop_listening(transcript):
        active_controller.pause()
        _reenable_listen(notifier, active_controller)
        return iter(())

    if matches_start_listening(transcript):
        active_controller.resume()
        _reenable_listen(notifier, active_controller)
        return iter(())

    if active_controller.paused:
        logger.info("Ignored while paused: %s", transcript)
        _reenable_listen(notifier, active_controller)
        return iter(())

    if language_code:
        logger.info("Transcription completed (language=%s): %s", language_code, transcript)
    else:
        logger.info("Transcription completed: %s", transcript)

    from buddy_tools.voice.endpointing import PendingUtterance, commit_voice_turn, process_with_endpointing_gate

    gated = process_with_endpointing_gate(
        notifier,
        transcript=transcript,
        language_code=language_code,
        turn_id=turn_id,
        turn_revision=turn_revision,
        speech_stopped_at_s=speech_stopped_at_s,
    )
    if gated is not None:
        _reenable_listen(notifier, active_controller)
        return gated

    if notifier.runtime_config is not None:
        return commit_voice_turn(
            notifier,
            PendingUtterance(
                transcript=transcript,
                language_code=language_code,
                turn_id=turn_id,
                turn_revision=turn_revision,
                speech_stopped_at_s=speech_stopped_at_s,
            ),
        )

    return iter(())
