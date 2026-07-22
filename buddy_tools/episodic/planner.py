"""Heuristic recall planner for episodic memory retrieval (v1)."""

from __future__ import annotations

import re
from typing import Any, Literal

from buddy_tools.episodic.dates import extract_relative_date_from_query_now

RecallDepth = Literal["period", "session", "turns", "day"]

_PERIOD_PATTERNS = (
    r"\beverything\s+about\b",
    r"\ball\s+(?:about|times)\b",
    r"\bover\s+the\s+years\b",
    r"\bhistory\s+of\b",
    r"\ball\s+our\s+(?:talks?|conversations?|discussions?)\b",
    r"\bevery\s+time\s+we\b",
)

_TURNS_PATTERNS = (
    r"\bexactly\s+what\s+(?:did\s+)?i\s+say\b",
    r"\bwhat\s+exactly\s+did\s+i\s+say\b",
    r"\bwhat\s+did\s+i\s+say\b",
    r"\bverbatim\b",
    r"\bquote\b",
    r"\bword\s+for\s+word\b",
    r"\bexact\s+words?\b",
)

_SESSION_ID_RE = re.compile(r"\d{8}T\d{6}-[a-f0-9]{8}")
_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")


def plan_episodic_recall(query: str) -> dict[str, Any]:
    """Classify query depth and recommend follow-up episodic tools."""
    query_clean = query.strip()
    query_lower = query_clean.lower()

    resolved_date = extract_relative_date_from_query_now(query_clean)
    if resolved_date is not None:
        return {
            "depth": "day",
            "reason": (
                f"Query references a relative calendar day — read the day summary for {resolved_date}."
            ),
            "recommended_tools": ["episodic"],
            "recommended_args": {
                "action": "read_summary",
                "level": "day",
                "date": resolved_date,
            },
            "resolved_date": resolved_date,
        }

    session_id_match = _SESSION_ID_RE.search(query_clean)
    if session_id_match:
        return {
            "depth": "turns",
            "reason": "Query references a specific session id — load raw turns for detail.",
            "recommended_tools": ["episodic"],
            "recommended_args": {
                "action": "read_turns",
                "session_id": session_id_match.group(0),
            },
        }

    date_match = _DATE_RE.search(query_clean)
    if date_match:
        absolute_date = date_match.group(0)
        return {
            "depth": "day",
            "reason": (
                f"Query references calendar date {absolute_date} — read the day summary first."
            ),
            "recommended_tools": ["episodic"],
            "recommended_args": {
                "action": "read_summary",
                "level": "day",
                "date": absolute_date,
            },
            "resolved_date": absolute_date,
        }

    for pattern in _TURNS_PATTERNS:
        if re.search(pattern, query_lower):
            return {
                "depth": "turns",
                "reason": "Query asks for verbatim or exact wording from a conversation.",
                "recommended_tools": ["episodic"],
                "recommended_args": {"action": "read_turns"},
            }

    for pattern in _PERIOD_PATTERNS:
        if re.search(pattern, query_lower):
            return {
                "depth": "period",
                "reason": "Broad query spanning multiple sessions — start with year or month summaries.",
                "recommended_tools": ["episodic"],
                "recommended_args": {"action": "read_summary"},
            }

    if re.search(r"\bwhen\s+did\s+we\b", query_lower) or re.search(r"\blast\s+time\b", query_lower):
        return {
            "depth": "session",
            "reason": "Temporal recall question — review matching session summaries first.",
            "recommended_tools": ["episodic"],
            "recommended_args": {"action": "read_summary", "level": "session"},
        }

    return {
        "depth": "session",
        "reason": "Default to session-level summaries before loading turns.",
        "recommended_tools": ["episodic"],
        "recommended_args": {"action": "read_summary", "level": "session"},
    }
