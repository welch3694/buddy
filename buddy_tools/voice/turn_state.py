"""Voice turn-state observability for logs and live transcription (#84)."""

from __future__ import annotations

import logging
import threading
from enum import Enum
from queue import Queue
from typing import Any

from speech_to_speech.pipeline.events import PartialTranscriptionEvent

logger = logging.getLogger(__name__)


class VoiceTurnState(str, Enum):
    LISTENING = "listening"
    HOLDING = "holding"
    GENERATING = "generating"
    SPEAKING = "speaking"
    PAUSED = "paused"


HOLDING_STATUS_MESSAGE = "[holding — waiting for you to finish…]"
PAUSED_STATUS_MESSAGE = '[paused — say "start listening" to resume]'

_STATE_LOG_MESSAGES: dict[VoiceTurnState, str] = {
    VoiceTurnState.LISTENING: "Turn state: listening",
    VoiceTurnState.HOLDING: "Turn state: holding — waiting for you to finish",
    VoiceTurnState.GENERATING: "Turn state: generating",
    VoiceTurnState.SPEAKING: "Turn state: speaking",
    VoiceTurnState.PAUSED: 'Turn state: paused — speech ignored (say "start listening" to resume)',
}


class TurnStateController:
    """Tracks listening / holding / generating / speaking / paused with transition-only logs."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = VoiceTurnState.LISTENING
        self.text_output_queue: Queue[Any] | None = None

    @property
    def state(self) -> VoiceTurnState:
        with self._lock:
            return self._state

    def configure(self, *, text_output_queue: Queue[Any] | None = None) -> TurnStateController:
        if text_output_queue is not None:
            self.text_output_queue = text_output_queue
        return self

    def reset_for_tests(self) -> None:
        with self._lock:
            self._state = VoiceTurnState.LISTENING
            self.text_output_queue = None

    def set(
        self,
        state: VoiceTurnState,
        *,
        reason: str | None = None,
        turn_id: str | None = None,
        turn_revision: int | None = None,
        announce_ui: bool = False,
    ) -> bool:
        """Transition state. Returns True when the state changed."""
        with self._lock:
            if self._state is state:
                return False
            previous = self._state
            self._state = state
            queue = self.text_output_queue

        message = _STATE_LOG_MESSAGES[state]
        extras: list[str] = []
        if reason:
            extras.append(reason)
        if turn_id is not None:
            extras.append(f"turn={turn_id}")
        if turn_revision is not None:
            extras.append(f"rev={turn_revision}")
        if extras:
            logger.info("%s (%s) [%s → %s]", message, ", ".join(extras), previous.value, state.value)
        else:
            logger.info("%s [%s → %s]", message, previous.value, state.value)

        if announce_ui and queue is not None:
            status = _ui_status_for(state)
            if status is not None:
                queue.put(
                    PartialTranscriptionEvent(
                        delta=status,
                        turn_id=turn_id,
                        turn_revision=turn_revision,
                    )
                )

        from buddy_tools.companion.publisher import emit_turn_state

        emit_turn_state(
            state.value,
            reason=reason,
            turn_id=turn_id,
            turn_revision=turn_revision,
        )
        return True


def _ui_status_for(state: VoiceTurnState) -> str | None:
    if state is VoiceTurnState.HOLDING:
        return HOLDING_STATUS_MESSAGE
    if state is VoiceTurnState.PAUSED:
        return PAUSED_STATUS_MESSAGE
    if state is VoiceTurnState.GENERATING:
        return "[generating…]"
    if state is VoiceTurnState.SPEAKING:
        return "[speaking…]"
    if state is VoiceTurnState.LISTENING:
        return "[listening…]"
    return None


_controller = TurnStateController()


def get_turn_state_controller() -> TurnStateController:
    return _controller


def configure_turn_state(*, text_output_queue: Queue[Any] | None = None) -> TurnStateController:
    return get_turn_state_controller().configure(text_output_queue=text_output_queue)


def reset_turn_state_for_tests() -> None:
    get_turn_state_controller().reset_for_tests()


def set_turn_state(
    state: VoiceTurnState,
    *,
    reason: str | None = None,
    turn_id: str | None = None,
    turn_revision: int | None = None,
    announce_ui: bool = False,
) -> bool:
    return get_turn_state_controller().set(
        state,
        reason=reason,
        turn_id=turn_id,
        turn_revision=turn_revision,
        announce_ui=announce_ui,
    )


def current_turn_state() -> VoiceTurnState:
    return get_turn_state_controller().state
