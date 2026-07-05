"""Episodic session record schema and persistence."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from buddy_tools.episodic.paths import SESSION_FILENAME, turns_jsonl_path

logger = logging.getLogger(__name__)

SessionStatus = Literal["open", "closing", "closed"]
IdleReason = Literal["idle_timeout", "max_duration", "shutdown", "personality_switch"]

_VALID_STATUSES = frozenset({"open", "closing", "closed"})


@dataclass
class EpisodicSession:
    session_id: str
    status: SessionStatus
    started_at: str
    persona_namespace: str
    ended_at: str | None = None
    idle_reason: str | None = None
    channels: list[str] = field(default_factory=list)
    turn_count: int = 0
    summary: str = ""
    topics: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "session_id": self.session_id,
            "status": self.status,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "idle_reason": self.idle_reason,
            "channels": list(self.channels),
            "persona_namespace": self.persona_namespace,
            "turn_count": self.turn_count,
            "summary": self.summary,
            "topics": list(self.topics),
        }
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EpisodicSession:
        status = str(data.get("status", "")).strip()
        if status not in _VALID_STATUSES:
            raise ValueError(f"Invalid session status: {status!r}")

        channels_raw = data.get("channels", [])
        if not isinstance(channels_raw, list):
            raise ValueError("session channels must be a list")

        topics_raw = data.get("topics", [])
        if not isinstance(topics_raw, list):
            raise ValueError("session topics must be a list")

        ended_at = data.get("ended_at")
        idle_reason = data.get("idle_reason")

        return cls(
            session_id=str(data["session_id"]).strip(),
            status=status,  # type: ignore[arg-type]
            started_at=str(data["started_at"]).strip(),
            ended_at=str(ended_at).strip() if ended_at else None,
            idle_reason=str(idle_reason).strip() if idle_reason else None,
            channels=[str(entry) for entry in channels_raw],
            persona_namespace=str(data["persona_namespace"]).strip(),
            turn_count=int(data.get("turn_count", 0)),
            summary=str(data.get("summary", "")),
            topics=[str(entry) for entry in topics_raw],
        )


def load_session(path: Path) -> EpisodicSession | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("session.json must be an object")
        return EpisodicSession.from_dict(data)
    except (json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
        logger.warning("Could not load episodic session from %s: %s", path, exc)
        return None


def save_session(path: Path, session: EpisodicSession) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(session.to_dict(), indent=2) + "\n", encoding="utf-8")


def write_turns_placeholder(session_directory: Path) -> Path:
    """Create an empty turns.jsonl placeholder for Phase 2 turn logging."""
    path = turns_jsonl_path(session_directory)
    if not path.exists():
        path.write_text("", encoding="utf-8")
    return path


def find_session_json_files(episodic_tree: Path) -> list[Path]:
    """Return all session.json paths under an episodic tree."""
    if not episodic_tree.is_dir():
        return []
    return sorted(episodic_tree.rglob(SESSION_FILENAME))
