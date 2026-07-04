"""Per-turn reply channel metadata for multi-channel routing."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Literal

ChannelKind = Literal["voice", "telegram"]


@dataclass(frozen=True)
class TurnReplyContext:
    channel: ChannelKind
    telegram_chat_id: int | None = None
    telegram_message_thread_id: int | None = None


_lock = Lock()
_turn_contexts: dict[str, TurnReplyContext] = {}


def register_turn(turn_id: str, context: TurnReplyContext) -> None:
    with _lock:
        _turn_contexts[turn_id] = context


def get_turn(turn_id: str | None) -> TurnReplyContext | None:
    if turn_id is None:
        return None
    with _lock:
        return _turn_contexts.get(turn_id)


def clear_turn(turn_id: str | None) -> None:
    if turn_id is None:
        return
    with _lock:
        _turn_contexts.pop(turn_id, None)


def reset_turn_contexts() -> None:
    """Clear all registered turns (for tests)."""
    with _lock:
        _turn_contexts.clear()
