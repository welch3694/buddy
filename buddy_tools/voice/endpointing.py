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

from buddy_tools.voice.turn_completion_heuristic import (
    TurnCompletionVerdict,
    classify_turn_completion_heuristic,
    get_heuristic_config,
)
from buddy_tools.voice.turn_state import VoiceTurnState, set_turn_state
from speech_to_speech.api.openai_realtime.runtime_config import RuntimeConfig
from speech_to_speech.pipeline.messages import GenerateResponseRequest
from speech_to_speech.pipeline.speculative_turns import SpeculativeTurnTracker

logger = logging.getLogger(__name__)

_RELEASE_POLL_S = 0.05
_DEFAULT_RELEASE_DELAY_S = 1.0
_CONFIGURED_TRACKER_LOGGED = False


def _tracker_gate_snapshot(
    tracker: SpeculativeTurnTracker | None,
    turn_id: str | None,
    turn_revision: int | None,
) -> str:
    if tracker is None or turn_id is None or turn_revision is None:
        return "tracker=none"
    pending_reopen = tracker.has_pending_reopen(turn_id, turn_revision)
    pending_reopen_or_grace = tracker.has_pending_reopen_or_grace(turn_id, turn_revision)
    committed = tracker.is_committed(turn_id, turn_revision)
    return (
        f"pending_reopen={pending_reopen} pending_reopen_or_grace={pending_reopen_or_grace} "
        f"committed={committed}"
    )


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
        self._continue_hold_count = 0
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
        if speculative_turns is not None:
            global _CONFIGURED_TRACKER_LOGGED
            if not _CONFIGURED_TRACKER_LOGGED:
                logger.info("Endpointing gate configured with SpeculativeTurnTracker")
                _CONFIGURED_TRACKER_LOGGED = True
        elif text_prompt_queue is not None or runtime_config is not None:
            logger.warning(
                "Endpointing gate configured without SpeculativeTurnTracker; voice turns commit immediately"
            )
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
            reason = "no_tracker" if tracker is None else "missing_turn_metadata"
            logger.info(
                "Endpointing passthrough (%s) turn=%s rev=%s chars=%d",
                reason,
                turn_id,
                turn_revision,
                len(transcript),
            )
            return _ObserveResult.PASSTHROUGH, None

        with self._lock:
            if self._pending is not None and self._pending.turn_id != turn_id:
                logger.info(
                    "Endpointing flushing prior pending turn=%s before turn=%s",
                    self._pending.turn_id,
                    turn_id,
                )
                self._flush_or_discard_pending_locked()

            if self._pending is not None and self._pending.turn_id == turn_id:
                if turn_revision > self._pending.turn_revision:
                    merged = merge_transcripts(self._pending.transcript, transcript)
                    logger.info(
                        "Endpointing merged turn=%s rev=%s->%s (%d -> %d chars)",
                        turn_id,
                        self._pending.turn_revision,
                        turn_revision,
                        len(self._pending.transcript),
                        len(merged),
                    )
                    self._pending = PendingUtterance(
                        turn_id=turn_id,
                        turn_revision=turn_revision,
                        transcript=merged,
                        language_code=language_code or self._pending.language_code,
                        speech_stopped_at_s=speech_stopped_at_s or self._pending.speech_stopped_at_s,
                    )
                    self._reset_continue_hold_count_locked()
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
                self._reset_continue_hold_count_locked()

            return self._evaluate_pending_locked()

    def _release_readiness_locked(
        self,
        pending: PendingUtterance,
    ) -> bool | None:
        tracker = self.speculative_turns
        assert tracker is not None
        return tracker.try_is_latest_after_reopen_grace(pending.turn_id, pending.turn_revision)

    def _evaluate_pending_locked(self) -> tuple[_ObserveResult, PendingUtterance | None]:
        pending = self._pending
        if pending is None:
            return _ObserveResult.HELD, None

        tracker = self.speculative_turns
        assert tracker is not None

        if tracker.is_committed(pending.turn_id, pending.turn_revision):
            logger.info(
                "Endpointing hold (already committed) turn=%s rev=%s [%s]",
                pending.turn_id,
                pending.turn_revision,
                _tracker_gate_snapshot(tracker, pending.turn_id, pending.turn_revision),
            )
            self._pending = None
            self._cancel_release_locked()
            return _ObserveResult.HELD, None

        ready = self._release_readiness_locked(pending)
        gate_state = _tracker_gate_snapshot(tracker, pending.turn_id, pending.turn_revision)
        if ready is True:
            result, utterance = self._try_release_pending_locked(pending, gate_state=gate_state)
            if result is _ObserveResult.COMMIT:
                logger.info(
                    "Endpointing commit sync turn=%s rev=%s chars=%d [%s]",
                    utterance.turn_id if utterance else pending.turn_id,
                    utterance.turn_revision if utterance else pending.turn_revision,
                    len(utterance.transcript) if utterance else len(pending.transcript),
                    gate_state,
                )
            return result, utterance
        if ready is False:
            logger.info(
                "Endpointing discard superseded turn=%s rev=%s [%s]",
                pending.turn_id,
                pending.turn_revision,
                gate_state,
            )
            self._pending = None
            self._cancel_release_locked()
            return _ObserveResult.HELD, None

        logger.info(
            "Endpointing hold turn=%s rev=%s chars=%d scheduling release in %.0fms [%s]",
            pending.turn_id,
            pending.turn_revision,
            len(pending.transcript),
            _RELEASE_POLL_S * 1000,
            gate_state,
        )
        self._schedule_release_locked(_RELEASE_POLL_S)
        set_turn_state(
            VoiceTurnState.HOLDING,
            reason="endpointing_gate",
            turn_id=pending.turn_id,
            turn_revision=pending.turn_revision,
            announce_ui=True,
        )
        return _ObserveResult.HELD, None

    def _flush_or_discard_pending_locked(self) -> None:
        pending = self._pending
        if pending is None:
            return
        tracker = self.speculative_turns
        if tracker is None:
            self._pending = None
            return
        if (
            self._release_readiness_locked(pending) is True
            and not tracker.is_committed(pending.turn_id, pending.turn_revision)
        ):
            result, utterance = self._try_release_pending_locked(pending)
            if result is _ObserveResult.COMMIT and utterance is not None:
                self._inject_commit_locked(utterance)
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

            ready = self._release_readiness_locked(pending)
            gate_state = _tracker_gate_snapshot(tracker, pending.turn_id, pending.turn_revision)
            if ready is True:
                result, utterance = self._try_release_pending_locked(pending, gate_state=gate_state)
                if result is _ObserveResult.COMMIT and utterance is not None:
                    logger.info(
                        "Endpointing commit timer turn=%s rev=%s [%s]",
                        utterance.turn_id,
                        utterance.turn_revision,
                        gate_state,
                    )
                    self._inject_commit_locked(utterance)
                return
            if ready is False:
                logger.info(
                    "Endpointing timer discard superseded turn=%s rev=%s [%s]",
                    pending.turn_id,
                    pending.turn_revision,
                    gate_state,
                )
                self._pending = None
                self._cancel_release_locked()
                return

            delay_s = _DEFAULT_RELEASE_DELAY_S if tracker.has_pending_reopen_or_grace(
                pending.turn_id,
                pending.turn_revision,
            ) else _RELEASE_POLL_S
            logger.info(
                "Endpointing timer still waiting turn=%s rev=%s reschedule in %.0fms [%s]",
                pending.turn_id,
                pending.turn_revision,
                delay_s * 1000,
                gate_state,
            )
            self._schedule_release_locked(delay_s)

    def _reset_continue_hold_count_locked(self) -> None:
        self._continue_hold_count = 0

    def _try_release_pending_locked(
        self,
        pending: PendingUtterance,
        *,
        gate_state: str | None = None,
    ) -> tuple[_ObserveResult, PendingUtterance | None]:
        """Run tier-1 heuristics before commit; extend grace on CONTINUE."""
        verdict = classify_turn_completion_heuristic(pending.transcript)
        if verdict is TurnCompletionVerdict.CONTINUE:
            cfg = get_heuristic_config()
            if self._continue_hold_count >= cfg.max_continue_holds:
                snapshot = gate_state
                if snapshot is None:
                    tracker = self.speculative_turns
                    if tracker is not None and pending.turn_id is not None and pending.turn_revision is not None:
                        snapshot = _tracker_gate_snapshot(tracker, pending.turn_id, pending.turn_revision)
                logger.info(
                    "Endpointing heuristic CONTINUE cap reached (%d) turn=%s rev=%s chars=%d committing [%s]",
                    cfg.max_continue_holds,
                    pending.turn_id,
                    pending.turn_revision,
                    len(pending.transcript),
                    snapshot or "tracker=none",
                )
            else:
                tracker = self.speculative_turns
                assert tracker is not None
                assert pending.turn_id is not None
                assert pending.turn_revision is not None
                hold_s = cfg.continue_hold_s
                tracker.start_reopen_grace(pending.turn_id, pending.turn_revision, hold_s)
                self._continue_hold_count += 1
                snapshot = gate_state or _tracker_gate_snapshot(
                    tracker,
                    pending.turn_id,
                    pending.turn_revision,
                )
                logger.info(
                    "Endpointing heuristic CONTINUE turn=%s rev=%s chars=%d hold=%.1fs count=%d/%d [%s]",
                    pending.turn_id,
                    pending.turn_revision,
                    len(pending.transcript),
                    hold_s,
                    self._continue_hold_count,
                    cfg.max_continue_holds,
                    snapshot,
                )
                self._schedule_release_locked(min(hold_s, _RELEASE_POLL_S))
                set_turn_state(
                    VoiceTurnState.HOLDING,
                    reason="heuristic_continue",
                    turn_id=pending.turn_id,
                    turn_revision=pending.turn_revision,
                    announce_ui=True,
                )
                return _ObserveResult.HELD, None

        utterance = pending
        self._pending = None
        self._cancel_release_locked()
        self._reset_continue_hold_count_locked()
        return _ObserveResult.COMMIT, utterance

    def _inject_commit_locked(self, utterance: PendingUtterance) -> None:
        runtime_config = self.runtime_config
        text_prompt_queue = self.text_prompt_queue
        tracker = self.speculative_turns
        if runtime_config is None or text_prompt_queue is None:
            logger.warning("Endpointing release dropped: scheduler not configured with runtime_config/queue")
            return

        from buddy_tools.pulse.state import is_silence_gated_only_active
        from buddy_tools.voice.barge_in import consume_barge_in_active
        from buddy_tools.voice.short_utterance_gate import should_discard_utterance

        discard_reason = should_discard_utterance(utterance.transcript)
        if discard_reason is not None:
            try:
                if tracker is not None:
                    tracker.commit(utterance.turn_id, utterance.turn_revision)
                _log_short_utterance_discard(utterance, discard_reason)
                set_turn_state(
                    VoiceTurnState.LISTENING,
                    reason="short_utterance_gate",
                    turn_id=utterance.turn_id,
                    turn_revision=utterance.turn_revision,
                )
            except Exception:
                logger.exception(
                    "Endpointing gate failed short-utterance discard for turn=%s",
                    utterance.turn_id,
                )
            return

        barge_in = consume_barge_in_active()
        if is_silence_gated_only_active() and not barge_in:
            try:
                perform_commit_side_effects(utterance)
                if tracker is not None:
                    tracker.commit(utterance.turn_id, utterance.turn_revision)
                logger.info(
                    "Voice commit suppressed: silence_gated_only active (turn=%s rev=%s)",
                    utterance.turn_id,
                    utterance.turn_revision,
                )
            except Exception:
                logger.exception(
                    "Endpointing gate failed suppressed commit side effects for turn=%s",
                    utterance.turn_id,
                )
            return

        request = build_commit_request(runtime_config, utterance)
        if request is None:
            return

        try:
            perform_commit_side_effects(utterance)
            text_prompt_queue.put(request)
            if tracker is not None:
                tracker.commit(utterance.turn_id, utterance.turn_revision)
            logger.info(
                "Endpointing gate released turn=%s rev=%s: %s",
                utterance.turn_id,
                utterance.turn_revision,
                utterance.transcript[:80],
            )
            set_turn_state(
                VoiceTurnState.GENERATING,
                reason="endpointing_release",
                turn_id=utterance.turn_id,
                turn_revision=utterance.turn_revision,
                announce_ui=True,
            )
        except Exception:
            logger.exception("Endpointing gate failed to inject commit for turn=%s", utterance.turn_id)

    def reset_for_tests(self) -> None:
        with self._lock:
            self._pending = None
            self._continue_hold_count = 0
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
    global _CONFIGURED_TRACKER_LOGGED
    gate = get_endpointing_gate()
    gate.reset_for_tests()
    gate.speculative_turns = None
    gate.text_prompt_queue = None
    gate.runtime_config = None
    gate.should_listen = None
    _CONFIGURED_TRACKER_LOGGED = False


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


def _log_short_utterance_discard(utterance: PendingUtterance, reason: Any) -> None:
    logger.info(
        "Voice commit discarded: short_utterance_gate reason=%s (turn=%s rev=%s): %s",
        reason,
        utterance.turn_id,
        utterance.turn_revision,
        utterance.transcript[:80],
    )


def build_commit_request(
    runtime_config: RuntimeConfig,
    utterance: PendingUtterance,
) -> GenerateResponseRequest | None:
    from openai.types.realtime.realtime_response_create_params import RealtimeResponseCreateParams
    from openai.types.responses.tool_choice_function import ToolChoiceFunction
    from speech_to_speech.LLM.chat import make_user_message

    from buddy_tools.pulse.inject import prepare_fold_cue_commit_instructions
    from buddy_tools.voice.action_intents import (
        clear_action_intent,
        match_action_intent,
        stash_action_intent,
    )

    runtime_config.chat.add_item(make_user_message(utterance.transcript))
    response = None
    intent = match_action_intent(utterance.transcript)
    if intent is not None:
        response = RealtimeResponseCreateParams(
            tool_choice=ToolChoiceFunction(type="function", name=intent.tool_name),
        )
        stash_action_intent(utterance.turn_id, intent)
        logger.info(
            "Action intent forced tool_choice=%s (turn=%s rev=%s)",
            intent.tool_name,
            utterance.turn_id,
            utterance.turn_revision,
        )
    else:
        clear_action_intent(utterance.turn_id)
        fold_instructions = prepare_fold_cue_commit_instructions(runtime_config)
        if fold_instructions is not None:
            response = RealtimeResponseCreateParams(instructions=fold_instructions)
            logger.info(
                "Fold pending mandatory cue into voice commit (turn=%s rev=%s)",
                utterance.turn_id,
                utterance.turn_revision,
            )
    return GenerateResponseRequest(
        runtime_config=runtime_config,
        language_code=utterance.language_code,
        turn_id=utterance.turn_id,
        turn_revision=utterance.turn_revision,
        speech_stopped_at_s=utterance.speech_stopped_at_s,
        response=response,
    )


def commit_voice_turn(
    notifier: Any,
    utterance: PendingUtterance,
) -> Iterator[GenerateResponseRequest]:
    """Commit a voice utterance synchronously through the transcription notifier path."""
    runtime_config = notifier.runtime_config
    if runtime_config is None:
        return iter(())

    from buddy_tools.pulse.state import is_silence_gated_only_active
    from buddy_tools.voice.barge_in import consume_barge_in_active
    from buddy_tools.voice.short_utterance_gate import should_discard_utterance

    discard_reason = should_discard_utterance(utterance.transcript)
    if discard_reason is not None:
        _log_short_utterance_discard(utterance, discard_reason)
        set_turn_state(
            VoiceTurnState.LISTENING,
            reason="short_utterance_gate",
            turn_id=utterance.turn_id,
            turn_revision=utterance.turn_revision,
        )
        return iter(())

    perform_commit_side_effects(utterance)
    barge_in = consume_barge_in_active()
    if is_silence_gated_only_active() and not barge_in:
        logger.info(
            "Voice commit suppressed: silence_gated_only active (turn=%s rev=%s)",
            utterance.turn_id,
            utterance.turn_revision,
        )
        set_turn_state(
            VoiceTurnState.LISTENING,
            reason="silence_gated_only",
            turn_id=utterance.turn_id,
            turn_revision=utterance.turn_revision,
        )
        return iter(())

    request = build_commit_request(runtime_config, utterance)
    if request is None:
        return iter(())
    set_turn_state(
        VoiceTurnState.GENERATING,
        reason="voice_commit",
        turn_id=utterance.turn_id,
        turn_revision=utterance.turn_revision,
        announce_ui=True,
    )
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
    if result is _ObserveResult.COMMIT and utterance is not None:
        iterator = commit_voice_turn(notifier, utterance)
        tracker = get_endpointing_gate().speculative_turns
        if tracker is not None and utterance.turn_id is not None and utterance.turn_revision is not None:
            tracker.commit(utterance.turn_id, utterance.turn_revision)
        return iterator
    if result is _ObserveResult.HELD:
        return iter(())
    logger.debug("Endpointing observe returned unexpected result=%s", result)
    return iter(())
