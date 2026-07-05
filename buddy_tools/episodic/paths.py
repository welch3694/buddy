"""Filesystem paths for per-persona episodic memory trees."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from buddy_tools.memory import persona_memory_dir

SESSIONS_DIRNAME = "sessions"
SESSION_FILENAME = "session.json"
TURNS_FILENAME = "turns.jsonl"
YEAR_ROLLUP_FILENAME = "year.json"
MONTH_ROLLUP_FILENAME = "month.json"
DAY_ROLLUP_FILENAME = "day.json"


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def bucket_keys(now: datetime, tz: ZoneInfo) -> tuple[str, str, str]:
    """Return (year, year-month, year-month-day) directory names in the given timezone."""
    local = now.astimezone(tz)
    year = f"{local.year:04d}"
    year_month = f"{year}-{local.month:02d}"
    year_month_day = f"{year_month}-{local.day:02d}"
    return year, year_month, year_month_day


def session_id_for(now: datetime, tz: ZoneInfo) -> str:
    """Stable, sortable session id: local timestamp prefix + random suffix."""
    local = now.astimezone(tz)
    prefix = local.strftime("%Y%m%dT%H%M%S")
    suffix = uuid.uuid4().hex[:8]
    return f"{prefix}-{suffix}"


def episodic_root(memory_root: Path, persona_namespace: str) -> Path:
    return persona_memory_dir(memory_root, persona_namespace) / "episodic"


def year_dir(memory_root: Path, persona_namespace: str, year: str) -> Path:
    return episodic_root(memory_root, persona_namespace) / year


def month_dir(memory_root: Path, persona_namespace: str, year: str, year_month: str) -> Path:
    return year_dir(memory_root, persona_namespace, year) / year_month


def day_dir(
    memory_root: Path,
    persona_namespace: str,
    year: str,
    year_month: str,
    year_month_day: str,
) -> Path:
    return month_dir(memory_root, persona_namespace, year, year_month) / year_month_day


def sessions_dir(
    memory_root: Path,
    persona_namespace: str,
    year: str,
    year_month: str,
    year_month_day: str,
) -> Path:
    return day_dir(memory_root, persona_namespace, year, year_month, year_month_day) / SESSIONS_DIRNAME


def session_dir(
    memory_root: Path,
    persona_namespace: str,
    year: str,
    year_month: str,
    year_month_day: str,
    session_id: str,
) -> Path:
    return sessions_dir(memory_root, persona_namespace, year, year_month, year_month_day) / session_id


def session_json_path(session_directory: Path) -> Path:
    return session_directory / SESSION_FILENAME


def turns_jsonl_path(session_directory: Path) -> Path:
    return session_directory / TURNS_FILENAME


def year_rollup_path(memory_root: Path, persona_namespace: str, year: str) -> Path:
    return year_dir(memory_root, persona_namespace, year) / YEAR_ROLLUP_FILENAME


def month_rollup_path(
    memory_root: Path,
    persona_namespace: str,
    year: str,
    year_month: str,
) -> Path:
    return month_dir(memory_root, persona_namespace, year, year_month) / MONTH_ROLLUP_FILENAME


def day_rollup_path(
    memory_root: Path,
    persona_namespace: str,
    year: str,
    year_month: str,
    year_month_day: str,
) -> Path:
    return day_dir(memory_root, persona_namespace, year, year_month, year_month_day) / DAY_ROLLUP_FILENAME


def ensure_session_directories(
    memory_root: Path,
    persona_namespace: str,
    year: str,
    year_month: str,
    year_month_day: str,
    session_id: str,
) -> Path:
    """Create day/month/year/session dirs for a new session (lazy — only when opening)."""
    directory = session_dir(
        memory_root,
        persona_namespace,
        year,
        year_month,
        year_month_day,
        session_id,
    )
    directory.mkdir(parents=True, exist_ok=True)
    return directory
