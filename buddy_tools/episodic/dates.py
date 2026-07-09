"""Resolve relative calendar dates for episodic memory retrieval."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from buddy_tools.episodic.config import load_episodic_config

_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_N_DAYS_AGO_RE = re.compile(r"^(\d+)\s+days?\s+ago$", re.IGNORECASE)
_QUERY_N_DAYS_AGO_RE = re.compile(r"\b(\d+)\s+days?\s+ago\b", re.IGNORECASE)

_RELATIVE_OFFSETS: dict[str, int] = {
    "today": 0,
    "yesterday": 1,
    "day before yesterday": 2,
}

_QUERY_RELATIVE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bday before yesterday\b", re.IGNORECASE), "day before yesterday"),
    (re.compile(r"\byesterday\b", re.IGNORECASE), "yesterday"),
    (re.compile(r"\btoday\b", re.IGNORECASE), "today"),
)


def _local_date(now: datetime, tz: ZoneInfo) -> datetime:
    return now.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)


def _format_local_date(local_midnight: datetime) -> str:
    return local_midnight.date().isoformat()


def resolve_episodic_date(value: str, *, now: datetime, tz: ZoneInfo) -> str | None:
    """Resolve an absolute or relative date string to YYYY-MM-DD in the episodic timezone."""
    text = value.strip()
    if not text:
        return None

    if _DAY_RE.match(text):
        return text

    lower = text.lower()
    if lower in _RELATIVE_OFFSETS:
        local = _local_date(now, tz)
        target = local - timedelta(days=_RELATIVE_OFFSETS[lower])
        return _format_local_date(target)

    days_ago = _N_DAYS_AGO_RE.match(lower)
    if days_ago is not None:
        offset = int(days_ago.group(1))
        if offset < 0 or offset > 30:
            return None
        local = _local_date(now, tz)
        target = local - timedelta(days=offset)
        return _format_local_date(target)

    return None


def extract_relative_date_from_query(query: str, *, now: datetime, tz: ZoneInfo) -> str | None:
    """Find and resolve the first relative date phrase in a natural-language query."""
    query_clean = query.strip()
    if not query_clean:
        return None

    for pattern, phrase in _QUERY_RELATIVE_PATTERNS:
        if pattern.search(query_clean):
            return resolve_episodic_date(phrase, now=now, tz=tz)

    days_ago = _QUERY_N_DAYS_AGO_RE.search(query_clean)
    if days_ago is not None:
        return resolve_episodic_date(days_ago.group(0), now=now, tz=tz)

    return None


def resolve_episodic_date_now(value: str) -> str | None:
    """Resolve a date string using the current time and episodic config timezone."""
    config = load_episodic_config()
    return resolve_episodic_date(value, now=datetime.now(UTC), tz=config.tzinfo)


def extract_relative_date_from_query_now(query: str) -> str | None:
    """Extract a relative date from a query using the current time and episodic timezone."""
    config = load_episodic_config()
    return extract_relative_date_from_query(query, now=datetime.now(UTC), tz=config.tzinfo)
