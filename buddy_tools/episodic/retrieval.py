"""Episodic memory retrieval tools — progressive disclosure over the temporal tree."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

from openai.types.realtime import RealtimeFunctionTool

from buddy_tools.core.groups import ToolGroup
from buddy_tools.core.result import ToolExecutionResult
from buddy_tools.core.tool_logging import safe_tool_context, tool_error
from buddy_tools.episodic.dates import extract_relative_date_from_query_now, resolve_episodic_date_now
from buddy_tools.episodic.paths import SESSIONS_DIRNAME, episodic_root, session_json_path, turns_jsonl_path
from buddy_tools.episodic.index import get_search_default_limit, search_index
from buddy_tools.episodic.planner import plan_episodic_recall
from buddy_tools.episodic.provenance import episodic_provenance, parse_session_location
from buddy_tools.episodic.regenerate import find_session_directory
from buddy_tools.episodic.rollup import load_day_rollup, load_month_rollup, load_year_rollup
from buddy_tools.episodic.session import EpisodicSession, find_session_json_files, load_session
from buddy_tools.episodic.turns import load_turns

_BLURB_MAX_LEN = 120
_TURNS_DEFAULT_LIMIT = 20
_TURNS_MAX_LIMIT = 50
_TOPIC_DEFAULT_LIMIT = 10
_TOPIC_MAX_LIMIT = 25
_SEARCH_DEFAULT_LIMIT = get_search_default_limit()
_SEARCH_MAX_LIMIT = 25

_YEAR_RE = re.compile(r"^\d{4}$")
_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

ListParent = Literal["root", "year", "month", "day"]
SummaryLevel = Literal["year", "month", "day", "session"]

EPISODIC_TOOL_DEFINITIONS: list[RealtimeFunctionTool] = [
    RealtimeFunctionTool(
        type="function",
        name="list_episodic_periods",
        description=(
            "Browse the episodic memory index for the active persona. Start at parent=root for "
            "years, then drill into year, month, and day to list sessions with short blurbs. "
            "For parent=day you may pass date alone (YYYY-MM-DD or relative such as today/"
            "yesterday) without browsing year/month first."
        ),
        parameters={
            "type": "object",
            "properties": {
                "parent": {
                    "type": "string",
                    "enum": ["root", "year", "month", "day"],
                    "description": "Index level to list children of",
                },
                "year": {
                    "type": "string",
                    "description": "Four-digit year, required when parent is year, month, or day "
                    "(auto-derived from date when parent is day)",
                },
                "month": {
                    "type": "string",
                    "description": "YYYY-MM month key, required when parent is month or day "
                    "(auto-derived from date when parent is day)",
                },
                "date": {
                    "type": "string",
                    "description": "YYYY-MM-DD or relative term (yesterday, today, N days ago); "
                    "required when parent is day",
                },
            },
            "required": ["parent"],
        },
    ),
    RealtimeFunctionTool(
        type="function",
        name="read_episodic_summary",
        description=(
            "Read a consolidated summary JSON for a year, month, day, or session in the active "
            "persona's episodic tree. For level=day, date accepts YYYY-MM-DD or relative terms "
            "such as yesterday or today (resolved in the episodic timezone). For level=session, "
            "pass a real session id (YYYYMMDDTHHMMSS-xxxxxxxx), or omit session_id to load the "
            "latest session for date (default today). Do not pass a calendar date as session_id."
        ),
        parameters={
            "type": "object",
            "properties": {
                "level": {
                    "type": "string",
                    "enum": ["year", "month", "day", "session"],
                },
                "year": {"type": "string", "description": "Four-digit year"},
                "month": {"type": "string", "description": "YYYY-MM month key"},
                "date": {
                    "type": "string",
                    "description": "YYYY-MM-DD date key, or relative term (yesterday, today, N days ago)",
                },
                "session_id": {
                    "type": "string",
                    "description": (
                        "Real session id (YYYYMMDDTHHMMSS-xxxxxxxx). Required only when targeting "
                        "a specific session; omit with level=session to load the latest session "
                        "for date (default today). Never pass YYYY-MM-DD here."
                    ),
                },
            },
            "required": ["level"],
        },
    ),
    RealtimeFunctionTool(
        type="function",
        name="read_episodic_turns",
        description=(
            "Read paginated raw conversation turns from turns.jsonl for one episodic session. "
            "session_id must be a real session id (YYYYMMDDTHHMMSS-xxxxxxxx), not a calendar date."
        ),
        parameters={
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Target session id (YYYYMMDDTHHMMSS-xxxxxxxx), not YYYY-MM-DD",
                },
                "offset": {
                    "type": "integer",
                    "description": "Zero-based turn offset (default 0)",
                },
                "limit": {
                    "type": "integer",
                    "description": f"Max turns to return (default {_TURNS_DEFAULT_LIMIT}, max {_TURNS_MAX_LIMIT})",
                },
            },
            "required": ["session_id"],
        },
    ),
    RealtimeFunctionTool(
        type="function",
        name="search_episodic_memory",
        description=(
            "Semantic search over episodic session and period summaries for the active persona. "
            "Use for fuzzy recall when topic tags or exact keywords are insufficient. "
            "Results include scores, provenance, and a recall_plan for drill-down."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural-language question or topic to search for",
                },
                "limit": {
                    "type": "integer",
                    "description": (
                        f"Max results (default {_SEARCH_DEFAULT_LIMIT}, max {_SEARCH_MAX_LIMIT})"
                    ),
                },
            },
            "required": ["query"],
        },
    ),
    RealtimeFunctionTool(
        type="function",
        name="find_episodes_by_topic",
        description=(
            "Search episodic session and day summaries by topic tag or keyword substring "
            "for the active persona."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Topic tag or keyword to search for",
                },
                "limit": {
                    "type": "integer",
                    "description": f"Max results (default {_TOPIC_DEFAULT_LIMIT}, max {_TOPIC_MAX_LIMIT})",
                },
            },
            "required": ["query"],
        },
    ),
]

EPISODIC_TOOL_NAMES = frozenset(tool.name for tool in EPISODIC_TOOL_DEFINITIONS)


def build_episodic_instructions() -> str:
    return (
        "Episodic memory stores past conversations in a temporal tree (year/month/day/session). "
        "It is NOT in the memory snapshot — use episodic tools to recall what was discussed when.\n"
        "- read_episodic_summary: load a day/month/year/session summary; for yesterday/today or a "
        "specific calendar day call this directly with level=day and date (do not browse the tree first); "
        "for earlier today / last session call level=session directly (optional date, default today) — "
        "do not pass a YYYY-MM-DD as session_id\n"
        "- search_episodic_memory: semantic search over summaries for fuzzy recall; follow recall_plan to drill down\n"
        "- list_episodic_periods: browse the index when you do not know the target date or period; "
        "to choose among sessions on one day call parent=day with date (relative OK) without year/month browse\n"
        "- read_episodic_turns: read raw turns for one session (paginated); needs a real session id "
        "(YYYYMMDDTHHMMSS-xxxxxxxx), never a calendar date\n"
        "- find_episodes_by_topic: exact topic tag or keyword substring match in summaries\n"
        "Use semantic memory tools (read_memory / snapshot) for durable facts; "
        "use episodic tools for conversation history and 'when did we talk about X' questions."
    )


EPISODIC_TOOL_GROUP = ToolGroup(
    id="episodic",
    title="Episodic memory",
    when_to_use=(
        "User asks about past conversations, when something was discussed, "
        "or wants to browse/search conversation history (not durable fact notes)."
    ),
    tools=tuple(EPISODIC_TOOL_DEFINITIONS),
    instructions=build_episodic_instructions(),
)


def _session_blurb(session: EpisodicSession) -> str:
    summary = session.summary.strip()
    if summary:
        if len(summary) <= _BLURB_MAX_LEN:
            return summary
        return summary[: _BLURB_MAX_LEN - 3] + "..."
    if session.status == "open":
        return f"(open session, {session.turn_count} turns)"
    return "(no summary yet)"


def _sorted_dir_names(directory: Path, pattern: re.Pattern[str]) -> list[str]:
    if not directory.is_dir():
        return []
    names = [entry.name for entry in directory.iterdir() if entry.is_dir() and pattern.match(entry.name)]
    return sorted(names)


def _reject_date_shaped_session_id(session_id: str) -> None:
    cleaned = session_id.strip()
    if _DAY_RE.match(cleaned):
        raise ValueError(
            f"session_id looks like a calendar date ({cleaned!r}). "
            "Use read_episodic_summary with level=day and that date, "
            "or list_episodic_periods(parent=day, date=...) then pass a real session id "
            "(YYYYMMDDTHHMMSS-xxxxxxxx)."
        )


def _resolve_day_date_arg(date: str | None, *, default: str | None = None) -> str:
    """Resolve a date argument to YYYY-MM-DD (absolute or relative)."""
    raw = (date or default or "").strip()
    if not raw:
        raise ValueError(
            "date must be YYYY-MM-DD or a supported relative term (yesterday, today, N days ago)"
        )
    if _DAY_RE.match(raw):
        return raw
    resolved = resolve_episodic_date_now(raw)
    if resolved is None:
        raise ValueError(
            "date must be YYYY-MM-DD or a supported relative term (yesterday, today, N days ago)"
        )
    return resolved


def _load_day_sessions(
    memory_root: Path,
    persona_namespace: str,
    year: str,
    month: str,
    date: str,
) -> list[tuple[Path, EpisodicSession]]:
    tree = episodic_root(memory_root, persona_namespace)
    sessions_path = tree / year / month / date / SESSIONS_DIRNAME
    loaded: list[tuple[Path, EpisodicSession]] = []
    if not sessions_path.is_dir():
        return loaded
    for session_directory in sorted(entry for entry in sessions_path.iterdir() if entry.is_dir()):
        session = load_session(session_json_path(session_directory))
        if session is None:
            continue
        loaded.append((session_directory, session))
    return loaded


def _day_session_entries(
    memory_root: Path,
    persona_namespace: str,
    sessions: list[tuple[Path, EpisodicSession]],
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for session_directory, session in sessions:
        entries.append(
            {
                "id": session.session_id,
                "label": session.session_id,
                "blurb": _session_blurb(session),
                "status": session.status,
                "provenance": episodic_provenance(
                    memory_root,
                    persona_namespace,
                    session_directory,
                    session_id=session.session_id,
                ),
            }
        )
    return entries


def _pick_latest_day_session(
    sessions: list[tuple[Path, EpisodicSession]],
) -> tuple[Path, EpisodicSession, str]:
    """Pick latest session by id; prefer closed when latest is open and a closed sibling exists."""
    ordered = sorted(sessions, key=lambda item: item[1].session_id, reverse=True)
    latest_directory, latest_session = ordered[0]
    if latest_session.status == "open":
        closed = [item for item in ordered if item[1].status == "closed"]
        if closed:
            return closed[0][0], closed[0][1], "latest_closed"
    return latest_directory, latest_session, "latest"


def list_episodic_periods(
    memory_root: Path,
    persona_namespace: str,
    *,
    parent: ListParent,
    year: str | None = None,
    month: str | None = None,
    date: str | None = None,
) -> dict[str, Any]:
    tree = episodic_root(memory_root, persona_namespace)

    if parent == "root":
        years = _sorted_dir_names(tree, _YEAR_RE)
        entries = [{"id": entry, "label": entry, "child_count": _child_count(tree / entry)} for entry in years]
        return {"parent": parent, "entries": entries}

    if parent == "day":
        date = _resolve_day_date_arg(date)
        year = year or date[:4]
        month = month or date[:7]

    if not year and month and _MONTH_RE.match(month):
        year = month.split("-", 1)[0]

    if not year or not _YEAR_RE.match(year):
        raise ValueError("year is required and must be YYYY when parent is year, month, or day")

    if parent == "year":
        year_path = tree / year
        months = _sorted_dir_names(year_path, _MONTH_RE)
        entries = [{"id": entry, "label": entry, "child_count": _child_count(year_path / entry)} for entry in months]
        return {"parent": parent, "year": year, "entries": entries}

    if not month or not _MONTH_RE.match(month):
        raise ValueError("month is required and must be YYYY-MM when parent is month or day")
    if not month.startswith(f"{year}-"):
        raise ValueError(f"month {month!r} does not belong to year {year!r}")

    if parent == "month":
        month_path = tree / year / month
        days = _sorted_dir_names(month_path, _DAY_RE)
        entries = [{"id": entry, "label": entry, "child_count": _child_count(month_path / entry)} for entry in days]
        return {"parent": parent, "year": year, "month": month, "entries": entries}

    if not date or not _DAY_RE.match(date):
        raise ValueError("date is required and must be YYYY-MM-DD when parent is day")
    if not date.startswith(f"{month}-"):
        raise ValueError(f"date {date!r} does not belong to month {month!r}")

    sessions = _load_day_sessions(memory_root, persona_namespace, year, month, date)
    entries = _day_session_entries(memory_root, persona_namespace, sessions)
    return {"parent": parent, "year": year, "month": month, "date": date, "entries": entries}


def _child_count(directory: Path) -> int:
    if not directory.is_dir():
        return 0
    return sum(1 for entry in directory.iterdir() if entry.is_dir())


def read_episodic_summary(
    memory_root: Path,
    persona_namespace: str,
    *,
    level: SummaryLevel,
    year: str | None = None,
    month: str | None = None,
    date: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    tree = episodic_root(memory_root, persona_namespace)

    if level == "year":
        if not year or not _YEAR_RE.match(year):
            raise ValueError("year is required and must be YYYY when level is year")
        path = tree / year / "year.json"
        payload = load_year_rollup(memory_root, persona_namespace, year)
        if not payload:
            raise ValueError(f"no year summary found for {year!r}")
        return {"level": level, "summary": payload, "provenance": episodic_provenance(memory_root, persona_namespace, path)}

    if level == "month":
        if not year or not _YEAR_RE.match(year):
            raise ValueError("year is required when level is month")
        if not month or not _MONTH_RE.match(month):
            raise ValueError("month is required and must be YYYY-MM when level is month")
        path = tree / year / month / "month.json"
        payload = load_month_rollup(memory_root, persona_namespace, year, month)
        if not payload:
            raise ValueError(f"no month summary found for {month!r}")
        return {"level": level, "summary": payload, "provenance": episodic_provenance(memory_root, persona_namespace, path)}

    if level == "day":
        if date and not _DAY_RE.match(date):
            resolved = resolve_episodic_date_now(date)
            if resolved is None:
                raise ValueError(
                    "date must be YYYY-MM-DD or a supported relative term (yesterday, today, N days ago)"
                )
            date = resolved
        if date and _DAY_RE.match(date):
            year = year or date[:4]
            month = month or date[:7]
        if not year or not _YEAR_RE.match(year):
            raise ValueError("year is required when level is day")
        if not month or not _MONTH_RE.match(month):
            raise ValueError("month is required when level is day")
        if not date or not _DAY_RE.match(date):
            raise ValueError("date is required and must be YYYY-MM-DD when level is day")
        path = tree / year / month / date / "day.json"
        payload = load_day_rollup(memory_root, persona_namespace, year, month, date)
        if not payload:
            raise ValueError(f"no day summary found for {date!r}")
        return {"level": level, "summary": payload, "provenance": episodic_provenance(memory_root, persona_namespace, path)}

    if level == "session":
        if session_id and session_id.strip():
            cleaned_id = session_id.strip()
            _reject_date_shaped_session_id(cleaned_id)
            session_directory = find_session_directory(memory_root, persona_namespace, cleaned_id)
            if session_directory is None:
                raise ValueError(f"session not found: {cleaned_id!r}")
            session = load_session(session_json_path(session_directory))
            if session is None:
                raise ValueError(f"session not found: {cleaned_id!r}")
            return {
                "level": level,
                "summary": session.to_dict(),
                "provenance": episodic_provenance(
                    memory_root,
                    persona_namespace,
                    session_directory,
                    session_id=session.session_id,
                ),
            }

        resolved_date = _resolve_day_date_arg(date, default="today")
        year = resolved_date[:4]
        month = resolved_date[:7]
        day_sessions = _load_day_sessions(memory_root, persona_namespace, year, month, resolved_date)
        if not day_sessions:
            raise ValueError(f"no sessions found for {resolved_date!r}")
        session_directory, session, selection = _pick_latest_day_session(day_sessions)
        siblings = [
            {
                "id": sibling.session_id,
                "blurb": _session_blurb(sibling),
                "status": sibling.status,
            }
            for _, sibling in sorted(day_sessions, key=lambda item: item[1].session_id)
        ]
        return {
            "level": level,
            "resolved_date": resolved_date,
            "selection": selection,
            "summary": session.to_dict(),
            "siblings": siblings,
            "provenance": episodic_provenance(
                memory_root,
                persona_namespace,
                session_directory,
                session_id=session.session_id,
            ),
        }

    raise ValueError(f"unsupported level: {level!r}")


def read_episodic_turns(
    memory_root: Path,
    persona_namespace: str,
    *,
    session_id: str,
    offset: int = 0,
    limit: int = _TURNS_DEFAULT_LIMIT,
) -> dict[str, Any]:
    if not session_id or not session_id.strip():
        raise ValueError("session_id is required")

    cleaned_id = session_id.strip()
    _reject_date_shaped_session_id(cleaned_id)

    if offset < 0:
        raise ValueError("offset must be >= 0")

    if limit < 1:
        raise ValueError("limit must be >= 1")
    limit = min(limit, _TURNS_MAX_LIMIT)

    session_directory = find_session_directory(memory_root, persona_namespace, cleaned_id)
    if session_directory is None:
        raise ValueError(f"session not found: {cleaned_id!r}")

    turns_path = turns_jsonl_path(session_directory)
    all_turns = load_turns(turns_path)
    total_count = len(all_turns)
    page = all_turns[offset : offset + limit]

    return {
        "session_id": cleaned_id,
        "turns": page,
        "total_count": total_count,
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(page) < total_count,
        "provenance": episodic_provenance(
            memory_root,
            persona_namespace,
            turns_path,
            session_id=cleaned_id,
        ),
    }


def _topic_matches(query: str, session: EpisodicSession) -> tuple[bool, str]:
    query_norm = query.strip().lower()
    if not query_norm:
        return False, ""

    for topic in session.topics:
        topic_norm = topic.strip().lower()
        if query_norm in topic_norm or topic_norm in query_norm:
            snippet = session.summary.strip() or f"Topic: {topic}"
            return True, snippet[:_BLURB_MAX_LEN]

    summary = session.summary.strip()
    if summary and query_norm in summary.lower():
        return True, summary[:_BLURB_MAX_LEN]

    return False, ""


def _day_summary_matches(
    memory_root: Path,
    persona_namespace: str,
    year: str,
    year_month: str,
    year_month_day: str,
    query: str,
) -> tuple[bool, str]:
    rollup = load_day_rollup(memory_root, persona_namespace, year, year_month, year_month_day)
    summary = str(rollup.get("summary", "")).strip()
    query_norm = query.strip().lower()
    if summary and query_norm in summary.lower():
        return True, summary[:_BLURB_MAX_LEN]
    return False, ""


def find_episodes_by_topic(
    memory_root: Path,
    persona_namespace: str,
    *,
    query: str,
    limit: int = _TOPIC_DEFAULT_LIMIT,
) -> dict[str, Any]:
    query_clean = query.strip()
    if not query_clean:
        raise ValueError("query is required")

    if limit < 1:
        raise ValueError("limit must be >= 1")
    limit = min(limit, _TOPIC_MAX_LIMIT)

    tree = episodic_root(memory_root, persona_namespace)
    hits: list[dict[str, Any]] = []
    seen_session_ids: set[str] = set()
    seen_days: set[str] = set()

    for session_path in find_session_json_files(tree):
        session = load_session(session_path)
        if session is None:
            continue

        matched, snippet = _topic_matches(query_clean, session)
        if not matched:
            continue

        session_directory = session_path.parent
        location = parse_session_location(session_directory)
        date = location[2] if location else None

        if session.session_id in seen_session_ids:
            continue
        seen_session_ids.add(session.session_id)

        hits.append(
            {
                "match_type": "session",
                "session_id": session.session_id,
                "date": date,
                "topics": list(session.topics),
                "snippet": snippet,
                "provenance": episodic_provenance(
                    memory_root,
                    persona_namespace,
                    session_directory,
                    session_id=session.session_id,
                ),
            }
        )
        if len(hits) >= limit:
            break

    if len(hits) < limit:
        for day_path in sorted(tree.rglob("day.json")):
            day_key = str(day_path)
            if day_key in seen_days:
                continue
            seen_days.add(day_key)

            try:
                relative = day_path.relative_to(tree)
                parts = relative.parts
                if len(parts) != 4 or parts[3] != "day.json":
                    continue
                year, year_month, year_month_day = parts[0], parts[1], parts[2]
            except ValueError:
                continue

            matched, snippet = _day_summary_matches(
                memory_root,
                persona_namespace,
                year,
                year_month,
                year_month_day,
                query_clean,
            )
            if not matched:
                continue

            hits.append(
                {
                    "match_type": "day",
                    "date": year_month_day,
                    "snippet": snippet,
                    "provenance": episodic_provenance(memory_root, persona_namespace, day_path),
                }
            )
            if len(hits) >= limit:
                break

    return {"query": query_clean, "results": hits}


def search_episodic_memory(
    memory_root: Path,
    persona_namespace: str,
    *,
    query: str,
    limit: int = _SEARCH_DEFAULT_LIMIT,
) -> dict[str, Any]:
    query_clean = query.strip()
    if not query_clean:
        raise ValueError("query is required")

    if limit < 1:
        raise ValueError("limit must be >= 1")
    limit = min(limit, _SEARCH_MAX_LIMIT)

    recall_plan = plan_episodic_recall(query_clean)
    results = search_index(memory_root, persona_namespace, query_clean, limit=limit)
    payload: dict[str, Any] = {
        "query": query_clean,
        "recall_plan": recall_plan,
        "results": results,
    }
    resolved_date = recall_plan.get("resolved_date")
    if isinstance(resolved_date, str):
        payload["resolved_dates"] = [resolved_date]
    elif (extracted := extract_relative_date_from_query_now(query_clean)) is not None:
        payload["resolved_dates"] = [extracted]
    return payload


def execute_episodic_tool(
    memory_root: Path,
    persona_namespace: str,
    tool_name: str,
    args: dict[str, Any],
) -> ToolExecutionResult:
    try:
        if tool_name == "list_episodic_periods":
            payload = list_episodic_periods(
                memory_root,
                persona_namespace,
                parent=str(args.get("parent", "root")),  # type: ignore[arg-type]
                year=args.get("year"),
                month=args.get("month"),
                date=args.get("date"),
            )
            return ToolExecutionResult(output=json.dumps(payload))

        if tool_name == "read_episodic_summary":
            payload = read_episodic_summary(
                memory_root,
                persona_namespace,
                level=str(args.get("level", "")),  # type: ignore[arg-type]
                year=args.get("year"),
                month=args.get("month"),
                date=args.get("date"),
                session_id=args.get("session_id"),
            )
            return ToolExecutionResult(output=json.dumps(payload))

        if tool_name == "read_episodic_turns":
            offset_raw = args.get("offset", 0)
            limit_raw = args.get("limit", _TURNS_DEFAULT_LIMIT)
            payload = read_episodic_turns(
                memory_root,
                persona_namespace,
                session_id=str(args.get("session_id", "")),
                offset=int(offset_raw) if offset_raw is not None else 0,
                limit=int(limit_raw) if limit_raw is not None else _TURNS_DEFAULT_LIMIT,
            )
            return ToolExecutionResult(output=json.dumps(payload))

        if tool_name == "search_episodic_memory":
            limit_raw = args.get("limit", _SEARCH_DEFAULT_LIMIT)
            payload = search_episodic_memory(
                memory_root,
                persona_namespace,
                query=str(args.get("query", "")),
                limit=int(limit_raw) if limit_raw is not None else _SEARCH_DEFAULT_LIMIT,
            )
            return ToolExecutionResult(output=json.dumps(payload))

        if tool_name == "find_episodes_by_topic":
            limit_raw = args.get("limit", _TOPIC_DEFAULT_LIMIT)
            payload = find_episodes_by_topic(
                memory_root,
                persona_namespace,
                query=str(args.get("query", "")),
                limit=int(limit_raw) if limit_raw is not None else _TOPIC_DEFAULT_LIMIT,
            )
            return ToolExecutionResult(output=json.dumps(payload))

        return tool_error(tool_name, f"unknown episodic tool {tool_name!r}", context=safe_tool_context(args))
    except ValueError as exc:
        return tool_error(tool_name, str(exc), context=safe_tool_context(args))
    except (TypeError, json.JSONDecodeError) as exc:
        return tool_error(tool_name, f"invalid arguments: {exc}", context=safe_tool_context(args))
