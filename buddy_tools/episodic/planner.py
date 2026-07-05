"""Heuristic recall planner for episodic memory retrieval (v1)."""

from __future__ import annotations

import re
from typing import Any, Literal

RecallDepth = Literal["period", "session", "turns"]

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

    if _SESSION_ID_RE.search(query_clean) or _DATE_RE.search(query_clean):
        return {
            "depth": "turns",
            "reason": "Query references a specific session id or date — load raw turns for detail.",
            "recommended_tools": ["read_episodic_turns"],
        }

    for pattern in _TURNS_PATTERNS:
        if re.search(pattern, query_lower):
            return {
                "depth": "turns",
                "reason": "Query asks for verbatim or exact wording from a conversation.",
                "recommended_tools": ["read_episodic_turns"],
            }

    for pattern in _PERIOD_PATTERNS:
        if re.search(pattern, query_lower):
            return {
                "depth": "period",
                "reason": "Broad query spanning multiple sessions — start with year or month summaries.",
                "recommended_tools": ["read_episodic_summary"],
            }

    if re.search(r"\bwhen\s+did\s+we\b", query_lower) or re.search(r"\blast\s+time\b", query_lower):
        return {
            "depth": "session",
            "reason": "Temporal recall question — review matching session summaries first.",
            "recommended_tools": ["read_episodic_summary"],
        }

    return {
        "depth": "session",
        "reason": "Default to session-level summaries before loading turns.",
        "recommended_tools": ["read_episodic_summary"],
    }
