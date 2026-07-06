"""Shared provenance helpers for episodic memory tools and index."""

from __future__ import annotations

import re
from pathlib import Path

from buddy_tools.episodic.paths import SESSIONS_DIRNAME, episodic_root

_YEAR_RE = re.compile(r"^\d{4}$")
_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def relative_episodic_path(memory_root: Path, persona_namespace: str, path: Path) -> str:
    root = episodic_root(memory_root, persona_namespace).resolve()
    try:
        return str(path.resolve().relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(path)


def episodic_provenance(
    memory_root: Path,
    persona_namespace: str,
    path: Path,
    *,
    session_id: str | None = None,
) -> dict[str, str]:
    payload: dict[str, str] = {
        "path": relative_episodic_path(memory_root, persona_namespace, path),
    }
    if session_id:
        payload["session_id"] = session_id
    return payload


def parse_session_location(session_directory: Path) -> tuple[str, str, str] | None:
    """Return (year, year_month, year_month_day) from a session directory path."""
    try:
        session_id_dir = session_directory.name
        sessions_dir = session_directory.parent
        if sessions_dir.name != SESSIONS_DIRNAME:
            return None
        day_dir = sessions_dir.parent
        month_dir = day_dir.parent
        year_dir = month_dir.parent
        year = year_dir.name
        year_month = month_dir.name
        year_month_day = day_dir.name
        if not (_YEAR_RE.match(year) and _MONTH_RE.match(year_month) and _DAY_RE.match(year_month_day)):
            return None
        if session_id_dir:
            return year, year_month, year_month_day
    except (AttributeError, IndexError):
        return None
    return None
