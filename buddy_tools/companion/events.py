"""Companion status event builders and pulse snapshot helpers (#115)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from buddy_tools.pulse.state import PulseState

TURN_STATES = frozenset({"listening", "holding", "generating", "speaking", "paused"})


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def turn_state_event(
    state: str,
    *,
    reason: str | None = None,
    turn_id: str | None = None,
    turn_revision: int | None = None,
    ts: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": "turn_state",
        "state": state,
        "ts": ts or _utc_now_iso(),
    }
    if reason is not None:
        payload["reason"] = reason
    if turn_id is not None:
        payload["turn_id"] = turn_id
    if turn_revision is not None:
        payload["turn_revision"] = turn_revision
    return payload


def assistant_text_event(
    text: str,
    *,
    turn_id: str | None = None,
    turn_revision: int | None = None,
    ts: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": "assistant_text",
        "text": text,
        "ts": ts or _utc_now_iso(),
    }
    if turn_id is not None:
        payload["turn_id"] = turn_id
    if turn_revision is not None:
        payload["turn_revision"] = turn_revision
    return payload


def salient_pulse_snapshot(state: PulseState | None) -> dict[str, Any]:
    """Return panel-safe pulse fields (no full ``session_config`` dump)."""
    if state is None:
        return {"type": "pulse_state", "active": False, "ts": _utc_now_iso()}

    payload: dict[str, Any] = {
        "type": "pulse_state",
        "active": True,
        "skill_name": state.skill_name,
        "status": state.status,
        "phase": state.phase,
        "pulse_mode": state.pulse_mode,
        "pending_cue": state.pending_cue,
        "cue_priority": state.cue_priority,
        "pulse_in_flight": state.pulse_in_flight,
        "narrator_muted": state.narrator_muted,
        "tick_count": state.tick_count,
        "started_at": state.started_at,
        "last_tick_at": state.last_tick_at,
        "vars": dict(state.vars),
        "ts": _utc_now_iso(),
    }
    cameras = state.session_config.get("cameras") if isinstance(state.session_config, dict) else None
    if isinstance(cameras, dict) and cameras:
        # Labels only — keep the snapshot small for the HUD.
        payload["camera_labels"] = {
            str(key): (value.get("label") if isinstance(value, dict) else None)
            for key, value in cameras.items()
        }
    return payload


def pulse_state_event(state: PulseState | None) -> dict[str, Any]:
    return salient_pulse_snapshot(state)
