"""Non-blocking companion event publisher (#115).

Hot-path callers use ``put_nowait`` on a bounded queue. The bridge daemon always
drains the queue (even with zero clients) so producers never block on VAD/TTS.
"""

from __future__ import annotations

import logging
import threading
from queue import Empty, Full, Queue
from typing import Any

from buddy_tools.companion.events import assistant_text_event, pulse_state_event, turn_state_event
from buddy_tools.pulse.state import PulseState

logger = logging.getLogger(__name__)

DEFAULT_QUEUE_SIZE = 256

_publisher: CompanionEventPublisher | None = None
_publisher_lock = threading.Lock()


class CompanionEventPublisher:
    """Bounded fan-out queue for companion status JSON events."""

    def __init__(self, *, maxsize: int = DEFAULT_QUEUE_SIZE) -> None:
        self._queue: Queue[dict[str, Any]] = Queue(maxsize=max(1, maxsize))
        self._lock = threading.Lock()
        self._latest_turn_state: dict[str, Any] | None = None
        self._latest_pulse_state: dict[str, Any] | None = None

    def emit(self, event: dict[str, Any]) -> None:
        """Enqueue an event without blocking. Drops oldest on overflow."""
        event_type = event.get("type")
        with self._lock:
            if event_type == "turn_state":
                self._latest_turn_state = dict(event)
            elif event_type == "pulse_state":
                self._latest_pulse_state = dict(event)

        while True:
            try:
                self._queue.put_nowait(event)
                return
            except Full:
                try:
                    self._queue.get_nowait()
                except Empty:
                    return

    def emit_turn_state(
        self,
        state: str,
        *,
        reason: str | None = None,
        turn_id: str | None = None,
        turn_revision: int | None = None,
    ) -> None:
        self.emit(
            turn_state_event(
                state,
                reason=reason,
                turn_id=turn_id,
                turn_revision=turn_revision,
            )
        )

    def emit_assistant_text(
        self,
        text: str,
        *,
        turn_id: str | None = None,
        turn_revision: int | None = None,
    ) -> None:
        if not text:
            return
        self.emit(
            assistant_text_event(
                text,
                turn_id=turn_id,
                turn_revision=turn_revision,
            )
        )

    def emit_pulse_state(self, state: PulseState | None) -> None:
        self.emit(pulse_state_event(state))

    def try_get(self) -> dict[str, Any] | None:
        try:
            return self._queue.get_nowait()
        except Empty:
            return None

    def drain(self) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        while True:
            event = self.try_get()
            if event is None:
                break
            events.append(event)
        return events

    def snapshot_events(self) -> list[dict[str, Any]]:
        """Current turn + pulse snapshots for newly connected clients."""
        with self._lock:
            snapshots: list[dict[str, Any]] = []
            if self._latest_turn_state is not None:
                snapshots.append(dict(self._latest_turn_state))
            if self._latest_pulse_state is not None:
                snapshots.append(dict(self._latest_pulse_state))
            return snapshots

    def qsize(self) -> int:
        return self._queue.qsize()


def get_companion_publisher() -> CompanionEventPublisher | None:
    return _publisher


def set_companion_publisher(publisher: CompanionEventPublisher | None) -> None:
    global _publisher
    with _publisher_lock:
        _publisher = publisher


def reset_companion_publisher_for_tests() -> None:
    set_companion_publisher(None)


def emit_turn_state(
    state: str,
    *,
    reason: str | None = None,
    turn_id: str | None = None,
    turn_revision: int | None = None,
) -> None:
    publisher = get_companion_publisher()
    if publisher is None:
        return
    publisher.emit_turn_state(
        state,
        reason=reason,
        turn_id=turn_id,
        turn_revision=turn_revision,
    )


def emit_assistant_text(
    text: str,
    *,
    turn_id: str | None = None,
    turn_revision: int | None = None,
) -> None:
    publisher = get_companion_publisher()
    if publisher is None:
        return
    publisher.emit_assistant_text(
        text,
        turn_id=turn_id,
        turn_revision=turn_revision,
    )


def emit_pulse_state(state: PulseState | None) -> None:
    publisher = get_companion_publisher()
    if publisher is None:
        return
    publisher.emit_pulse_state(state)
