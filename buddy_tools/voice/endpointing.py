"""Hold final STT in a pending buffer until speculative reopen grace clears."""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from enum import Enum
from queue import Queue
from threading import Event, Timer
from typing import Any

from speech_to_speech.api.openai_realtime.runtime_config import RuntimeConfig
from speech_to_speech.pipeline.messages import GenerateResponseRequest
from speech_to_speech.pipeline.speculative_turns import SpeculativeTurnTracker

logger = logging.getLogger(__name__)

_RELEASE_POLL_S = 0.05
_DEFAULT_RELEASE_DELAY_S = 1.0


def merge_transcripts(existing: str, new: str) -> str:
    """Merge continued utterance text into pending buffer."""
    existing = existing.strip()
    new = new.strip()
    if not existing:
        return new
    if not new:
        return existing
    if new.startswith(existing) or existing in new:
        return new
    if existing.startswith(new):
        return existing
    return f"{existing} {new}".strip()


@dataclass(frozen=True)
class PendingUtterance:
    transcript: str
    language_code: str | None
    speech_stopped_at_s: float | None
    turn_id: str | None = None
    turn_revision: int | None = None


class _ObserveResult(Enum):
    PASSTHROUGH = "passthrough"
    HELD = "held"
    COMMIT = "commit"


class EndpointingGate:
    """Pending buffer between final STT and chat/LLM commit."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: PendingUtterance | None = None
        self._release_timer: Timer | None = None
        self.speculative_turns: SpeculativeTurnTracker | None = None
        self.text_prompt_queue: Queue[Any] | None = None
        self.runtime_config: RuntimeConfig | None = None
        self.should_listen: Event | None = None

    def configure(
        self,
        *,
        text_prompt_queue: Queue[Any] | None = None,
        runtime_config: RuntimeConfig | None = None,
        speculative_turns: SpeculativeTurnTracker | None = None,
        should_listen: Event | None = None,
    ) -> EndpointingGate:
        with self._lock:
            if text_prompt_queue is not None:
                self.text_prompt_queue = text_prompt_queue
            if runtime_config is not None:
                self.runtime_config = runtime_config
            if speculative_turns is not None:
                self.speculative_turns = speculative_turns
            if should_listen is not None:
                self.should_listen = should_listen
        return self

    def observe_final(
        self,
        *,
        transcript: str,
        language_code: str | None,
        turn_id: str | None,
        turn_revision: int | None,
        speech_stopped_at_s: float | None,
    ) -> tuple[_ObserveResult, PendingUtterance | None]:
        tracker = self.speculative_turns
        if tracker is None or turn_id is None or turn_revision is None:
            return _ObserveResult.PASSTHROUGH, None

        with self._lock:
            if self._pending is not None and self._pending.turn_id != turn_id:
                self._flush_or_discard_pending_locked()

            if self._pending is not None and self._pending.turn_id == turn_id:
                if turn_revision > self._pending.turn_revision:
                    merged = merge_transcripts(self._pending.transcript, transcript)
                    self._pending = PendingUtterance(
                        turn_id=turn_id,
                        turn_revision=turn_revision,
                        transcript=merged,
                        language_code=language_code or self._pending.language_code,
                        speech_stopped_at_s=speech_stopped_at_s or self._pending.speech_stopped_at_s,
                    )
                elif turn_revision < self._pending.turn_revision:
                    logger.debug(
                        "Ignoring stale final transcription turn=%s rev=%s (pending rev=%s)",
                        turn_id,
                        turn_revision,
                        self._pending.turn_revision,
                    )
                    return _ObserveResult.HELD, None
            else:
                self._pending = PendingUtterance(
                    turn_id=turn_id,
                    turn_revision=turn_revision,
                    transcript=transcript,
                    language_code=language_code,
                    speech_stopped_at_s=speech_stopped_at_s,
                )

            return self._evaluate_pending_locked()

    def _evaluate_pending_locked(self) -> tuple[_ObserveResult, PendingUtterance | None]:
        pending = self._pending
        if pending is None:
            return _ObserveResult.HELD, None

        tracker = self.speculative_turns
        assert tracker is not None

        if tracker.is_committed(pending.turn_id, pending.turn_revision):
            self._pending = None
            self._cancel_release_locked()
            return _ObserveResult.HELD, None

        commit_result = tracker.try_commit_if_latest_after_reopen_grace(
            pending.turn_id,
            pending.turn_revision,
        )
        if commit_result is True:
            utterance = pending
            self._pending = None
            self._cancel_release_locked()
            return _ObserveResult.COMMIT, utterance
        if commit_result is False:
            self._pending = None
            self._cancel_release_locked()
            return _ObserveResult.HELD, None

        self._schedule_release_locked(_RELEASE_POLL_S)
        return _ObserveResult.HELD, None

    def _flush_or_discard_pending_locked(self) -> None:
        pending = self._pending
        if pending is None:
            return
        tracker = self.speculative_turns
        if tracker is None:
            self._pending = None
            return
        if tracker.try_commit_if_latest_after_reopen_grace(pending.turn_id, pending.turn_revision) is True:
            self._inject_commit_locked(pending)
        self._pending = None
        self._cancel_release_locked()

    def _schedule_release_locked(self, delay_s: float) -> None:
        self._cancel_release_locked()
        delay = max(delay_s, _RELEASE_POLL_S)
        timer = Timer(delay, self._on_release_timer)
        timer.daemon = True
        self._release_timer = timer
        timer.start()

    def _cancel_release_locked(self) -> None:
        timer = self._release_timer
        if timer is not None:
            timer.cancel()
            self._release_timer = None

    def _on_release_timer(self) -> None:
        from buddy_tools.voice.listening_pause import get_listening_pause_controller

        if get_listening_pause_controller().paused:
            with self._lock:
                self._pending = None
                self._cancel_release_locked()
            return

        with self._lock:
            pending = self._pending
            if pending is None:
                return

            tracker = self.speculative_turns
            if tracker is None:
                return

            if tracker.is_committed(pending.turn_id, pending.turn_revision):
                self._pending = None
                self._cancel_release_locked()
                return

            commit_result = tracker.try_commit_if_latest_after_reopen_grace(
                pending.turn_id,
                pending.turn_revision,
            )
            if commit_result is True:
                utterance = pending
                self._pending = None
                self._cancel_release_locked()
                self._inject_commit_locked(utterance)
                return
            if commit_result is False:
                self._pending = None
                self._cancel_release_locked()
                return

            if tracker.has_pending_reopen_or_grace(pending.turn_id, pending.turn_revision):
                self._schedule_release_locked(_DEFAULT_RELEASE_DELAY_S)
            else:
                self._schedule_release_locked(_RELEASE_POLL_S)

    def _inject_commit_locked(self, utterance: PendingUtterance) -> None:
        runtime_config = self.runtime_config
        text_prompt_queue = self.text_prompt_queue
        if runtime_config is None or text_prompt_queue is None:
            logger.warning("Endpointing release dropped: scheduler not configured with runtime_config/queue")
            return

        tracker = self.speculative_turns
        if tracker is not None and tracker.is_committed(utterance.turn_id, utterance.turn_revision):
            return

        request = build_commit_request(runtime_config, utterance)
        if request is None:
            return

        try:
            perform_commit_side_effects(utterance)
            text_prompt_queue.put(request)
            logger.info(
                "Endpointing gate released turn=%s rev=%s: %s",
                utterance.turn_id,
                utterance.turn_revision,
                utterance.transcript[:80],
            )
        except Exception:
            logger.exception("Endpointing gate failed to inject commit for turn=%s", utterance.turn_id)

    def reset_for_tests(self) -> None:
        with self._lock:
            self._pending = None
            self._cancel_release_locked()


_gate = EndpointingGate()


def get_endpointing_gate() -> EndpointingGate:
    return _gate


def configure_endpointing(
    *,
    text_prompt_queue: Queue[Any] | None = None,
    runtime_config: RuntimeConfig | None = None,
    speculative_turns: SpeculativeTurnTracker | None = None,
    should_listen: Event | None = None,
) -> EndpointingGate:
    return get_endpointing_gate().configure(
        text_prompt_queue=text_prompt_queue,
        runtime_config=runtime_config,
        speculative_turns=speculative_turns,
        should_listen=should_listen,
    )


def reset_endpointing_for_tests() -> None:
    gate = get_endpointing_gate()
    gate.reset_for_tests()
    gate.speculative_turns = None
    gate.text_prompt_queue = None
    gate.runtime_config = None
    gate.should_listen = None


def perform_commit_side_effects(utterance: PendingUtterance) -> None:
    """Notify timers/pulse and episodic logging at commit time only."""
    from buddy_tools.timers import notify_user_speech
    from buddy_tools.episodic import EpisodicTurnRecord, get_episodic_manager

    notify_user_speech(utterance.speech_stopped_at_s)

    if utterance.turn_id is None:
        return

    manager = get_episodic_manager()
    if manager is not None:
        manager.on_user_activity("voice")
        manager.log_turn(
            EpisodicTurnRecord(
                role="user",
                channel="voice",
                turn_id=utterance.turn_id,
                text=utterance.transcript,
            )
        )


def build_commit_request(
    runtime_config: RuntimeConfig,
    utterance: PendingUtterance,
) -> GenerateResponseRequest | None:
    from speech_to_speech.LLM.chat import make_user_message

    runtime_config.chat.add_item(make_user_message(utterance.transcript))
    return GenerateResponseRequest(
        runtime_config=runtime_config,
        language_code=utterance.language_code,
        turn_id=utterance.turn_id,
        turn_revision=utterance.turn_revision,
        speech_stopped_at_s=utterance.speech_stopped_at_s,
    )


def commit_voice_turn(
    notifier: Any,
    utterance: PendingUtterance,
) -> Iterator[GenerateResponseRequest]:
    """Commit a voice utterance synchronously through the transcription notifier path."""
    runtime_config = notifier.runtime_config
    if runtime_config is None:
        return iter(())

    perform_commit_side_effects(utterance)
    request = build_commit_request(runtime_config, utterance)
    if request is None:
        return iter(())
    return iter([request])


def process_with_endpointing_gate(
    notifier: Any,
    *,
    transcript: str,
    language_code: str | None,
    turn_id: str | None,
    turn_revision: int | None,
    speech_stopped_at_s: float | None,
) -> Iterator[GenerateResponseRequest] | None:
    """Return None to pass through, iter(()) when held, or GRR iterator when released."""
    result, utterance = get_endpointing_gate().observe_final(
        transcript=transcript,
        language_code=language_code,
        turn_id=turn_id,
        turn_revision=turn_revision,
        speech_stopped_at_s=speech_stopped_at_s,
    )
    if result is _ObserveResult.PASSTHROUGH:
        return None
    if result is _ObserveResult.HELD:
        return iter(())
    if result is _ObserveResult.COMMIT and utterance is not None:
        return commit_voice_turn(notifier, utterance)
    return iter(())
