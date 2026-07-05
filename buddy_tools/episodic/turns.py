"""Episodic turn records and JSONL persistence."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from buddy_tools.episodic.paths import session_json_path, turns_jsonl_path
from buddy_tools.episodic.session import EpisodicSession, save_session

logger = logging.getLogger(__name__)

TurnRole = Literal["user", "assistant", "tool"]
TurnChannel = Literal["voice", "telegram"]
ContentType = Literal["text", "photo"]

_TOOL_OUTPUT_PREVIEW_LEN = 120


@dataclass
class EpisodicTurnRecord:
    role: TurnRole
    channel: TurnChannel
    turn_id: str | None = None
    timestamp: str | None = None
    text: str = ""
    content_type: ContentType = "text"
    has_image: bool = False
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    tool_success: bool | None = None
    tool_output_preview: str | None = None
    seq: int | None = field(default=None, repr=False)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "seq": self.seq,
            "role": self.role,
            "channel": self.channel,
            "turn_id": self.turn_id,
            "timestamp": self.timestamp,
            "text": self.text,
        }
        if self.content_type != "text":
            payload["content_type"] = self.content_type
        if self.has_image:
            payload["has_image"] = True
        if self.role == "tool":
            if self.tool_name is not None:
                payload["tool_name"] = self.tool_name
            if self.tool_args is not None:
                payload["tool_args"] = self.tool_args
            if self.tool_success is not None:
                payload["tool_success"] = self.tool_success
            if self.tool_output_preview is not None:
                payload["tool_output_preview"] = self.tool_output_preview
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EpisodicTurnRecord:
        return cls(
            seq=int(data["seq"]) if data.get("seq") is not None else None,
            role=data["role"],  # type: ignore[arg-type]
            channel=data["channel"],  # type: ignore[arg-type]
            turn_id=data.get("turn_id"),
            timestamp=data.get("timestamp"),
            text=str(data.get("text", "")),
            content_type=data.get("content_type", "text"),  # type: ignore[arg-type]
            has_image=bool(data.get("has_image", False)),
            tool_name=data.get("tool_name"),
            tool_args=data.get("tool_args"),
            tool_success=data.get("tool_success"),
            tool_output_preview=data.get("tool_output_preview"),
        )


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def append_turn(
    session_directory: Path,
    session: EpisodicSession,
    record: EpisodicTurnRecord,
) -> None:
    """Append one turn line to turns.jsonl and persist updated session metadata."""
    if record.seq is None:
        raise ValueError("EpisodicTurnRecord.seq must be set before append_turn")

    if record.timestamp is None:
        record.timestamp = utc_now_iso()

    turns_path = turns_jsonl_path(session_directory)
    line = json.dumps(record.to_dict(), ensure_ascii=False)
    with turns_path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")

    session.turn_count = record.seq
    save_session(session_json_path(session_directory), session)


def load_turns(turns_path: Path) -> list[dict[str, Any]]:
    """Load all turn records from a turns.jsonl file (for tests and replay)."""
    if not turns_path.is_file():
        return []

    records: list[dict[str, Any]] = []
    for line in turns_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        data = json.loads(stripped)
        if isinstance(data, dict):
            records.append(data)
    return records


def truncate_tool_output(output: str, *, max_len: int = _TOOL_OUTPUT_PREVIEW_LEN) -> str:
    text = output.strip()
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."
