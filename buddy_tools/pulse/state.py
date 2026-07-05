"""Pulse session runtime state persisted per persona namespace."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from buddy_tools.memory import persona_memory_dir
from buddy_tools.pulse.schema import (
    SessionConfig,
    SessionValidationError,
    load_session_config,
    session_config_from_dict,
    session_config_to_dict,
)

logger = logging.getLogger(__name__)

_PULSE_STATE_FILENAME = "pulse_state.json"
PulseStatus = Literal["active", "paused"]
PulseMode = Literal["directed", "conversational"]
CuePriority = Literal["mandatory", "conversational"]


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


@dataclass
class PulseState:
    skill_name: str
    status: PulseStatus
    tick_count: int = 0
    started_at: str = ""
    last_tick_at: str | None = None
    phase: str = "running"
    tick_interval_seconds: float = 5.0
    pending_cue: str | None = None
    cue_priority: CuePriority | None = None
    pulse_mode: PulseMode = "directed"
    narrator_muted: bool = False
    fired_rules: list[str] = field(default_factory=list)
    vars: dict[str, Any] = field(default_factory=dict)
    session_config: dict[str, Any] = field(default_factory=dict)
    last_user_speech_at: str | None = None
    last_assistant_speech_at: str | None = None
    pending_cue_since: str | None = None
    pulse_in_flight: bool = False

    def __post_init__(self) -> None:
        if not self.started_at:
            self.started_at = _utc_now_iso()

    def get_session_config(self) -> SessionConfig | None:
        if not self.session_config:
            return None
        try:
            return session_config_from_dict(self.session_config)
        except SessionValidationError as exc:
            logger.warning("Stored pulse session_config invalid for %r: %s", self.skill_name, exc)
            return None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "skill_name": self.skill_name,
            "status": self.status,
            "tick_count": self.tick_count,
            "started_at": self.started_at,
            "phase": self.phase,
            "tick_interval_seconds": self.tick_interval_seconds,
            "pulse_mode": self.pulse_mode,
            "narrator_muted": self.narrator_muted,
            "fired_rules": list(self.fired_rules),
            "vars": dict(self.vars),
            "session_config": dict(self.session_config),
            "pulse_in_flight": self.pulse_in_flight,
        }
        if self.last_tick_at is not None:
            payload["last_tick_at"] = self.last_tick_at
        if self.pending_cue is not None:
            payload["pending_cue"] = self.pending_cue
        if self.cue_priority is not None:
            payload["cue_priority"] = self.cue_priority
        if self.last_user_speech_at is not None:
            payload["last_user_speech_at"] = self.last_user_speech_at
        if self.last_assistant_speech_at is not None:
            payload["last_assistant_speech_at"] = self.last_assistant_speech_at
        if self.pending_cue_since is not None:
            payload["pending_cue_since"] = self.pending_cue_since
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PulseState:
        status = str(data.get("status", "")).strip()
        if status not in ("active", "paused"):
            raise ValueError(f"Invalid pulse status: {status!r}")

        tick_interval = float(data.get("tick_interval_seconds", 5.0))
        if tick_interval <= 0:
            raise ValueError("tick_interval_seconds must be positive")

        pulse_mode = str(data.get("pulse_mode", "directed")).strip()
        if pulse_mode not in ("directed", "conversational"):
            raise ValueError(f"Invalid pulse_mode: {pulse_mode!r}")

        cue_priority_raw = data.get("cue_priority")
        cue_priority: CuePriority | None = None
        if cue_priority_raw is not None:
            cue_priority = str(cue_priority_raw).strip()  # type: ignore[assignment]
            if cue_priority not in ("mandatory", "conversational"):
                raise ValueError(f"Invalid cue_priority: {cue_priority_raw!r}")

        vars_data = data.get("vars")
        if vars_data is None and "session" in data:
            legacy_session = data.get("session", {})
            vars_data = legacy_session if isinstance(legacy_session, dict) else {}
        if not isinstance(vars_data, dict):
            raise ValueError("pulse_state vars must be an object")

        session_config = data.get("session_config", {})
        if not isinstance(session_config, dict):
            raise ValueError("pulse_state session_config must be an object")

        fired_rules_raw = data.get("fired_rules", [])
        if not isinstance(fired_rules_raw, list):
            raise ValueError("pulse_state fired_rules must be a list")

        last_tick_at = data.get("last_tick_at")
        pending_cue = data.get("pending_cue")
        last_user = data.get("last_user_speech_at")
        last_assistant = data.get("last_assistant_speech_at")
        pending_since = data.get("pending_cue_since")

        return cls(
            skill_name=str(data["skill_name"]).strip(),
            status=status,  # type: ignore[arg-type]
            tick_count=int(data.get("tick_count", 0)),
            started_at=str(data.get("started_at", "")).strip() or _utc_now_iso(),
            last_tick_at=str(last_tick_at).strip() if last_tick_at else None,
            phase=str(data.get("phase", "running")).strip() or "running",
            tick_interval_seconds=tick_interval,
            pending_cue=str(pending_cue) if pending_cue is not None else None,
            cue_priority=cue_priority,
            pulse_mode=pulse_mode,  # type: ignore[arg-type]
            narrator_muted=bool(data.get("narrator_muted", False)),
            fired_rules=[str(entry) for entry in fired_rules_raw],
            vars=dict(vars_data),
            session_config=dict(session_config),
            last_user_speech_at=str(last_user).strip() if last_user else None,
            last_assistant_speech_at=str(last_assistant).strip() if last_assistant else None,
            pending_cue_since=str(pending_since).strip() if pending_since else None,
            pulse_in_flight=bool(data.get("pulse_in_flight", False)),
        )


def pulse_state_path(memory_root: Path, persona_namespace: str) -> Path:
    persona_dir = persona_memory_dir(memory_root, persona_namespace)
    path = (persona_dir / _PULSE_STATE_FILENAME).resolve()
    if path.parent != persona_dir.resolve():
        raise ValueError("Invalid pulse state path")
    return path


def ensure_pulse_anchors(state: PulseState) -> None:
    """Backfill elapsed_since anchors for sessions created before init seeding."""
    anchor = state.started_at or _utc_now_iso()
    state.vars.setdefault("last_camera_switch_at", anchor)
    state.vars.setdefault("last_conversation_pulse_at", anchor)


def load_pulse_state(memory_root: Path, persona_namespace: str) -> PulseState | None:
    path = pulse_state_path(memory_root, persona_namespace)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("pulse_state.json must be an object")
        state = PulseState.from_dict(data)
        ensure_pulse_anchors(state)
        return state
    except (json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
        logger.warning("Could not load pulse state from %s: %s", path, exc)
        return None


def save_pulse_state(
    memory_root: Path,
    persona_namespace: str,
    state: PulseState,
) -> None:
    path = pulse_state_path(memory_root, persona_namespace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state.to_dict(), indent=2) + "\n", encoding="utf-8")
    logger.info(
        "Saved pulse state: skill=%r status=%r phase=%r ticks=%d pending_cue=%r",
        state.skill_name,
        state.status,
        state.phase,
        state.tick_count,
        state.pending_cue,
    )


def clear_pulse_state(memory_root: Path, persona_namespace: str) -> None:
    path = pulse_state_path(memory_root, persona_namespace)
    if path.is_file():
        path.unlink()
        logger.info("Cleared pulse state for namespace %r", persona_namespace)


def build_pulse_state_from_session(
    skill_name: str,
    session: SessionConfig,
) -> PulseState:
    vars_data = dict(session.init_set)
    phase = str(vars_data.pop("phase", "running")).strip() or "running"
    narrator_muted = bool(vars_data.pop("narrator_muted", False))
    started_at = _utc_now_iso()

    # Seed elapsed_since anchors so first rule interval is measured from session start.
    vars_data.setdefault("last_camera_switch_at", started_at)
    vars_data.setdefault("last_conversation_pulse_at", started_at)

    return PulseState(
        skill_name=skill_name,
        status="active",
        started_at=started_at,
        phase=phase,
        tick_interval_seconds=session.pulse.tick_interval_s,
        narrator_muted=narrator_muted,
        vars=vars_data,
        session_config=session_config_to_dict(session),
    )


def init_pulse_state_from_skill(skill_name: str, skill_directory: Path) -> PulseState:
    """Initialize pulse runtime state from validated references/session.yaml."""
    session = load_session_config(skill_directory, skill_name=skill_name)
    return build_pulse_state_from_session(skill_name, session)
