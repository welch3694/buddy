"""Stub rollup files at day, month, and year levels."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from buddy_tools.episodic.paths import (
    day_rollup_path,
    month_rollup_path,
    year_rollup_path,
)

logger = logging.getLogger(__name__)


def _load_rollup(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError as exc:
        logger.warning("Could not read rollup %s: %s", path, exc)
    return {}


def _save_rollup(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _append_session_id(existing: dict[str, Any], session_id: str) -> list[str]:
    raw = existing.get("session_ids", [])
    ids = [str(entry) for entry in raw] if isinstance(raw, list) else []
    if session_id not in ids:
        ids.append(session_id)
    return ids


def ensure_year_rollup(
    memory_root: Path,
    persona_namespace: str,
    year: str,
    session_id: str,
) -> None:
    path = year_rollup_path(memory_root, persona_namespace, year)
    existing = _load_rollup(path)
    payload = {
        "level": "year",
        "year": year,
        "session_ids": _append_session_id(existing, session_id),
        "summary": str(existing.get("summary", "")),
    }
    _save_rollup(path, payload)


def ensure_month_rollup(
    memory_root: Path,
    persona_namespace: str,
    year: str,
    year_month: str,
    session_id: str,
) -> None:
    path = month_rollup_path(memory_root, persona_namespace, year, year_month)
    existing = _load_rollup(path)
    payload = {
        "level": "month",
        "month": year_month,
        "session_ids": _append_session_id(existing, session_id),
        "summary": str(existing.get("summary", "")),
    }
    _save_rollup(path, payload)


def ensure_day_rollup(
    memory_root: Path,
    persona_namespace: str,
    year: str,
    year_month: str,
    year_month_day: str,
    session_id: str,
) -> None:
    path = day_rollup_path(memory_root, persona_namespace, year, year_month, year_month_day)
    existing = _load_rollup(path)
    payload = {
        "level": "day",
        "date": year_month_day,
        "session_ids": _append_session_id(existing, session_id),
        "summary": str(existing.get("summary", "")),
    }
    _save_rollup(path, payload)


def register_session_in_rollups(
    memory_root: Path,
    persona_namespace: str,
    year: str,
    year_month: str,
    year_month_day: str,
    session_id: str,
) -> None:
    """Create or update stub rollup files when a session opens."""
    ensure_year_rollup(memory_root, persona_namespace, year, session_id)
    ensure_month_rollup(memory_root, persona_namespace, year, year_month, session_id)
    ensure_day_rollup(memory_root, persona_namespace, year, year_month, year_month_day, session_id)
