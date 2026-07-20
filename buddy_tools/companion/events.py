"""Companion status event builders and pulse snapshot helpers (#115)."""

from __future__ import annotations

import json
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


def speaking_progress_event(
    *,
    progress: float,
    played_ms: int,
    total_ms: int,
    total_final: bool = False,
    ts: str | None = None,
) -> dict[str, Any]:
    clamped = min(1.0, max(0.0, float(progress)))
    return {
        "type": "speaking_progress",
        "progress": clamped,
        "played_ms": max(0, int(played_ms)),
        "total_ms": max(0, int(total_ms)),
        "total_final": bool(total_final),
        "ts": ts or _utc_now_iso(),
    }


TOOL_CALL_STATUSES = frozenset({"ok", "error", "skipped"})
TOOL_CALL_SOURCES = frozenset({"llm", "silent"})


def format_tool_call_summary(
    tool: str,
    status: str,
    args_summary: dict[str, Any] | None = None,
) -> str:
    """Short HUD line: ``tool · status`` plus one safe arg when present."""
    base = f"{tool} · {status}"
    if not args_summary:
        return base
    for key, value in args_summary.items():
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        if len(text) > 40:
            text = text[:37] + "..."
        return f"{base} · {key}={text}"
    return base


def tool_call_event(
    *,
    tool: str,
    status: str,
    summary: str,
    source: str = "llm",
    turn_id: str | None = None,
    ts: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": "tool_call",
        "tool": tool,
        "status": status,
        "summary": summary,
        "source": source if source in TOOL_CALL_SOURCES else "llm",
        "ts": ts or _utc_now_iso(),
    }
    if turn_id is not None:
        payload["turn_id"] = turn_id
    return payload


# Default SENSES rows when session.yaml omits ``panel.senses``.
_DEFAULT_SENSES: tuple[str, ...] = ("phase", "pulse_mode", "pending_cue")

_SENSE_LABELS: dict[str, str] = {
    "phase": "PHASE",
    "pulse_mode": "MODE",
    "pending_cue": "CUE",
    "current_camera": "CAMERA",
    "status": "STATUS",
    "pulse_in_flight": "FLIGHT",
    "narrator_muted": "MUTED",
    "skill_name": "SKILL",
}

# Top-level PulseState fields resolvable by panel.senses paths.
_TOP_LEVEL_SENSE_ATTRS: frozenset[str] = frozenset(
    {
        "skill_name",
        "status",
        "phase",
        "pulse_mode",
        "pending_cue",
        "cue_priority",
        "pulse_in_flight",
        "narrator_muted",
        "tick_count",
        "started_at",
        "last_tick_at",
    }
)


def _sense_label(path: str) -> str:
    mapped = _SENSE_LABELS.get(path)
    if mapped:
        return mapped
    return path.replace("_", " ").upper()


def _format_sense_value(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, str):
        return value.strip() or "—"
    if isinstance(value, (int, float)):
        return str(value)
    try:
        return json.dumps(value, default=str)
    except TypeError:
        return str(value)


def _camera_label_map(session_config: dict[str, Any] | None) -> dict[str, str]:
    """Build id→label from session cameras (list or legacy dict)."""
    if not isinstance(session_config, dict):
        return {}
    cameras = session_config.get("cameras")
    labels: dict[str, str] = {}
    if isinstance(cameras, list):
        for entry in cameras:
            if not isinstance(entry, dict):
                continue
            cam_id = entry.get("id")
            if cam_id is None:
                continue
            label = entry.get("label")
            if isinstance(label, str) and label.strip():
                labels[str(cam_id)] = label.strip()
    elif isinstance(cameras, dict):
        for key, value in cameras.items():
            if isinstance(value, dict):
                label = value.get("label")
                if isinstance(label, str) and label.strip():
                    labels[str(key)] = label.strip()
    return labels


def _resolve_sense_raw(state: PulseState, path: str) -> Any:
    if path in _TOP_LEVEL_SENSE_ATTRS:
        return getattr(state, path, None)
    return state.vars.get(path)


def _resolve_sense_display(state: PulseState, path: str, camera_labels: dict[str, str]) -> str:
    raw = _resolve_sense_raw(state, path)
    if path == "current_camera" and raw is not None:
        key = str(raw)
        return camera_labels.get(key) or key
    return _format_sense_value(raw)


def _panel_sense_paths(state: PulseState) -> tuple[str, ...]:
    session = state.session_config if isinstance(state.session_config, dict) else {}
    panel = session.get("panel") if isinstance(session.get("panel"), dict) else {}
    senses_raw = panel.get("senses") if isinstance(panel, dict) else None
    if isinstance(senses_raw, list):
        paths = [str(item).strip() for item in senses_raw if str(item).strip()]
        if paths:
            return tuple(paths)
    return _DEFAULT_SENSES


def build_senses_rows(state: PulseState) -> list[dict[str, str]]:
    """Project ``panel.senses`` paths into HUD rows ``{key, label, value}``."""
    camera_labels = _camera_label_map(
        state.session_config if isinstance(state.session_config, dict) else None
    )
    rows: list[dict[str, str]] = []
    for path in _panel_sense_paths(state):
        rows.append(
            {
                "key": path,
                "label": _sense_label(path),
                "value": _resolve_sense_display(state, path, camera_labels),
            }
        )
    return rows


def salient_pulse_snapshot(state: PulseState | None) -> dict[str, Any]:
    """Return panel-safe pulse fields (no full ``session_config`` dump)."""
    if state is None:
        return {"type": "pulse_state", "active": False, "ts": _utc_now_iso()}

    camera_labels = _camera_label_map(
        state.session_config if isinstance(state.session_config, dict) else None
    )
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
        "senses": build_senses_rows(state),
        "ts": _utc_now_iso(),
    }
    if camera_labels:
        payload["camera_labels"] = camera_labels
    return payload


def pulse_state_event(state: PulseState | None) -> dict[str, Any]:
    return salient_pulse_snapshot(state)


def persona_event(
    *,
    personality_id: str,
    name: str,
    memory_namespace: str,
    voice_id: str | None = None,
    ts: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": "persona",
        "id": personality_id,
        "name": name,
        "memory_namespace": memory_namespace,
        "ts": ts or _utc_now_iso(),
    }
    if voice_id is not None:
        payload["voice_id"] = voice_id
    return payload


def theme_event(
    *,
    theme_id: str,
    name: str,
    tokens: dict[str, str],
    ts: str | None = None,
) -> dict[str, Any]:
    return {
        "type": "theme",
        "id": theme_id,
        "name": name,
        "tokens": dict(tokens),
        "ts": ts or _utc_now_iso(),
    }
