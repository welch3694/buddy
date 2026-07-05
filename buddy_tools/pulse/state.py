"""Pulse session runtime state persisted per persona namespace."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from buddy_tools.memory import persona_memory_dir

logger = logging.getLogger(__name__)

_PULSE_STATE_FILENAME = "pulse_state.json"
PulseStatus = Literal["active", "paused"]


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
    session: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.started_at:
            self.started_at = _utc_now_iso()

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "skill_name": self.skill_name,
            "status": self.status,
            "tick_count": self.tick_count,
            "started_at": self.started_at,
            "phase": self.phase,
            "tick_interval_seconds": self.tick_interval_seconds,
            "session": self.session,
        }
        if self.last_tick_at is not None:
            payload["last_tick_at"] = self.last_tick_at
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PulseState:
        status = str(data.get("status", "")).strip()
        if status not in ("active", "paused"):
            raise ValueError(f"Invalid pulse status: {status!r}")
        session = data.get("session", {})
        if not isinstance(session, dict):
            raise ValueError("pulse_state session must be an object")
        tick_interval = float(data.get("tick_interval_seconds", 5.0))
        if tick_interval <= 0:
            raise ValueError("tick_interval_seconds must be positive")
        last_tick_at = data.get("last_tick_at")
        return cls(
            skill_name=str(data["skill_name"]).strip(),
            status=status,  # type: ignore[arg-type]
            tick_count=int(data.get("tick_count", 0)),
            started_at=str(data.get("started_at", "")).strip() or _utc_now_iso(),
            last_tick_at=str(last_tick_at).strip() if last_tick_at else None,
            phase=str(data.get("phase", "running")).strip() or "running",
            tick_interval_seconds=tick_interval,
            session=dict(session),
        )


def pulse_state_path(memory_root: Path, persona_namespace: str) -> Path:
    persona_dir = persona_memory_dir(memory_root, persona_namespace)
    path = (persona_dir / _PULSE_STATE_FILENAME).resolve()
    if path.parent != persona_dir.resolve():
        raise ValueError("Invalid pulse state path")
    return path


def load_pulse_state(memory_root: Path, persona_namespace: str) -> PulseState | None:
    path = pulse_state_path(memory_root, persona_namespace)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("pulse_state.json must be an object")
        return PulseState.from_dict(data)
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
        "Saved pulse state: skill=%r status=%r phase=%r ticks=%d",
        state.skill_name,
        state.status,
        state.phase,
        state.tick_count,
    )


def clear_pulse_state(memory_root: Path, persona_namespace: str) -> None:
    path = pulse_state_path(memory_root, persona_namespace)
    if path.is_file():
        path.unlink()
        logger.info("Cleared pulse state for namespace %r", persona_namespace)


def init_pulse_state_from_skill(skill_name: str, skill_directory: Path) -> PulseState:
    """Initialize pulse runtime state, optionally seeded from references/session.yaml."""
    import yaml

    session_config: dict[str, Any] = {}
    session_path = skill_directory / "references" / "session.yaml"
    if session_path.is_file():
        try:
            raw = yaml.safe_load(session_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                session_config = raw
            else:
                logger.warning("session.yaml in %s is not a mapping; using defaults", skill_directory)
        except (OSError, yaml.YAMLError) as exc:
            logger.warning("Could not load session.yaml from %s: %s", session_path, exc)

    tick_interval = float(session_config.get("tick_interval_seconds", 5.0))
    if tick_interval <= 0:
        tick_interval = 5.0
    phase = str(session_config.get("phase", "running")).strip() or "running"
    session_data = {
        key: value
        for key, value in session_config.items()
        if key not in ("tick_interval_seconds", "phase")
    }

    return PulseState(
        skill_name=skill_name,
        status="active",
        phase=phase,
        tick_interval_seconds=tick_interval,
        session=session_data,
    )
