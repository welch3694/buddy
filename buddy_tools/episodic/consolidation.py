"""Episodic consolidation: session summaries, rollups, and semantic fact extraction."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from buddy_tools.episodic.paths import (
    day_rollup_path,
    month_rollup_path,
    session_json_path,
    sessions_dir,
    turns_jsonl_path,
    year_rollup_path,
)
from buddy_tools.episodic.rollup import (
    load_day_rollup,
    load_month_rollup,
    load_year_rollup,
    save_rollup,
)
from buddy_tools.episodic.session import EpisodicSession, load_session, save_session
from buddy_tools.episodic.turns import EpisodicTurnRecord, load_turns
from buddy_tools.infra.llm_client import LlmFn, complete_chat
from buddy_tools.memory import MemoryScope, upsert_memory_fact

logger = logging.getLogger(__name__)

_SESSION_SUMMARY_SYSTEM = (
    "You summarize conversation sessions for long-term episodic memory. "
    "Respond with JSON only: {\"summary\": \"...\", \"topics\": [\"topic1\", \"topic2\"]}. "
    "Summary should be concise (2-6 sentences). Topics are short lowercase tags."
)

_ROLLUP_SYSTEM = (
    "You merge child summaries into a parent rollup summary for episodic memory. "
    "Respond with JSON only: {\"summary\": \"...\"}. "
    "Preserve important facts; avoid duplication."
)

_FACT_EXTRACTION_SYSTEM = (
    "Extract durable facts from a session summary for persistent memory. "
    "Respond with JSON only: {\"facts\": [{\"scope\": \"global\"|\"persona\", "
    "\"name\": \"notes\", \"topic\": \"...\", \"value\": \"...\"}]}. "
    "Use scope=global for universal user facts; scope=persona for role-specific facts. "
    "Return an empty facts array if nothing durable should be stored."
)

_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}")


def format_turns_for_prompt(turns: list[EpisodicTurnRecord]) -> str:
    """Serialize turns into a compact transcript for the LLM."""
    lines: list[str] = []
    for turn in turns:
        if turn.has_image:
            text = turn.text.strip() or "[image]"
        else:
            text = turn.text.strip()
        if turn.role == "tool":
            preview = turn.tool_output_preview or text
            name = turn.tool_name or "tool"
            lines.append(f"[tool:{name}] {preview}")
        elif turn.role == "user":
            lines.append(f"User ({turn.channel}): {text}")
        elif turn.role == "assistant":
            lines.append(f"Assistant: {text}")
    return "\n".join(lines)


def _parse_json_object(text: str) -> dict[str, Any]:
    match = _JSON_BLOCK_RE.search(text)
    if not match:
        raise ValueError(f"No JSON object found in LLM response: {text[:200]!r}")
    data = json.loads(match.group())
    if not isinstance(data, dict):
        raise ValueError("LLM JSON response must be an object")
    return data


def _merge_summary_prompt(child_label: str, children: list[tuple[str, str]]) -> str:
    parts = [f"Merge these {child_label} summaries into one parent summary:\n"]
    for label, summary in children:
        parts.append(f"--- {label} ---\n{summary}\n")
    return "\n".join(parts)


def _load_turn_records(session_dir: Path) -> list[EpisodicTurnRecord]:
    raw = load_turns(turns_jsonl_path(session_dir))
    return [EpisodicTurnRecord.from_dict(entry) for entry in raw]


def summarize_session(
    session_dir: Path,
    *,
    llm_fn: LlmFn | None = None,
) -> EpisodicSession:
    """Summarize raw turns and mark session closed. Raises on failure."""
    session_path = session_json_path(session_dir)
    session = load_session(session_path)
    if session is None:
        raise ValueError(f"Session not found: {session_path}")

    turns = _load_turn_records(session_dir)
    transcript = format_turns_for_prompt(turns)
    if not transcript.strip():
        session.summary = "(empty session)"
        session.topics = []
        session.status = "closed"
        save_session(session_path, session)
        return session

    user_prompt = (
        f"Session id: {session.session_id}\n"
        f"Started: {session.started_at}\n"
        f"Channels: {', '.join(session.channels) or 'unknown'}\n\n"
        f"Transcript:\n{transcript}"
    )
    raw = complete_chat(_SESSION_SUMMARY_SYSTEM, user_prompt, llm_fn=llm_fn)
    data = _parse_json_object(raw)
    session.summary = str(data.get("summary", "")).strip() or "(no summary)"
    topics_raw = data.get("topics", [])
    if isinstance(topics_raw, list):
        session.topics = [str(t).strip() for t in topics_raw if str(t).strip()]
    else:
        session.topics = []
    session.status = "closed"
    save_session(session_path, session)
    logger.info(
        "Consolidated session %r (%d topics)",
        session.session_id,
        len(session.topics),
    )
    return session


def _session_summaries_for_day(
    memory_root: Path,
    persona_namespace: str,
    year: str,
    year_month: str,
    year_month_day: str,
) -> list[tuple[str, str]]:
    day_data = load_day_rollup(
        memory_root, persona_namespace, year, year_month, year_month_day
    )
    session_ids = day_data.get("session_ids", [])
    if not isinstance(session_ids, list):
        return []

    summaries: list[tuple[str, str]] = []
    base = sessions_dir(memory_root, persona_namespace, year, year_month, year_month_day)
    for session_id in session_ids:
        session_path = session_json_path(base / str(session_id))
        session = load_session(session_path)
        if session is None or not session.summary.strip():
            continue
        summaries.append((session.session_id, session.summary))
    return summaries


def rollup_day(
    memory_root: Path,
    persona_namespace: str,
    year: str,
    year_month: str,
    year_month_day: str,
    *,
    llm_fn: LlmFn | None = None,
) -> str:
    """Merge session summaries into day.json. Returns the day summary."""
    path = day_rollup_path(memory_root, persona_namespace, year, year_month, year_month_day)
    existing = load_day_rollup(memory_root, persona_namespace, year, year_month, year_month_day)
    children = _session_summaries_for_day(
        memory_root, persona_namespace, year, year_month, year_month_day
    )
    if not children:
        return str(existing.get("summary", ""))

    if len(children) == 1:
        summary = children[0][1]
    else:
        user_prompt = _merge_summary_prompt("session", children)
        raw = complete_chat(_ROLLUP_SYSTEM, user_prompt, llm_fn=llm_fn)
        data = _parse_json_object(raw)
        summary = str(data.get("summary", "")).strip()

    payload = {
        "level": "day",
        "date": year_month_day,
        "session_ids": existing.get("session_ids", []),
        "summary": summary,
    }
    save_rollup(path, payload)
    return summary


def _day_summaries_for_month(
    memory_root: Path,
    persona_namespace: str,
    year: str,
    year_month: str,
) -> list[tuple[str, str]]:
    month_dir_path = path_for_month_dir(memory_root, persona_namespace, year, year_month)
    if not month_dir_path.is_dir():
        return []

    children: list[tuple[str, str]] = []
    for day_path in sorted(month_dir_path.iterdir()):
        if not day_path.is_dir() or day_path.name == "sessions":
            continue
        rollup_path = day_path / "day.json"
        if not rollup_path.is_file():
            continue
        data = json.loads(rollup_path.read_text(encoding="utf-8"))
        summary = str(data.get("summary", "")).strip()
        if summary:
            children.append((day_path.name, summary))
    return children


def path_for_month_dir(memory_root: Path, persona_namespace: str, year: str, year_month: str) -> Path:
    from buddy_tools.episodic.paths import month_dir

    return month_dir(memory_root, persona_namespace, year, year_month)


def rollup_month(
    memory_root: Path,
    persona_namespace: str,
    year: str,
    year_month: str,
    *,
    llm_fn: LlmFn | None = None,
) -> str:
    """Merge day summaries into month.json."""
    path = month_rollup_path(memory_root, persona_namespace, year, year_month)
    existing = load_month_rollup(memory_root, persona_namespace, year, year_month)
    children = _day_summaries_for_month(memory_root, persona_namespace, year, year_month)
    if not children:
        return str(existing.get("summary", ""))

    if len(children) == 1:
        summary = children[0][1]
    else:
        user_prompt = _merge_summary_prompt("day", children)
        raw = complete_chat(_ROLLUP_SYSTEM, user_prompt, llm_fn=llm_fn)
        data = _parse_json_object(raw)
        summary = str(data.get("summary", "")).strip()

    payload = {
        "level": "month",
        "month": year_month,
        "session_ids": existing.get("session_ids", []),
        "summary": summary,
    }
    save_rollup(path, payload)
    return summary


def _month_summaries_for_year(
    memory_root: Path,
    persona_namespace: str,
    year: str,
) -> list[tuple[str, str]]:
    from buddy_tools.episodic.paths import year_dir

    year_path = year_dir(memory_root, persona_namespace, year)
    if not year_path.is_dir():
        return []

    children: list[tuple[str, str]] = []
    for month_path in sorted(year_path.iterdir()):
        if not month_path.is_dir():
            continue
        rollup_path = month_path / "month.json"
        if not rollup_path.is_file():
            continue
        data = json.loads(rollup_path.read_text(encoding="utf-8"))
        summary = str(data.get("summary", "")).strip()
        if summary:
            children.append((month_path.name, summary))
    return children


def rollup_year(
    memory_root: Path,
    persona_namespace: str,
    year: str,
    *,
    llm_fn: LlmFn | None = None,
) -> str:
    """Merge month summaries into year.json."""
    path = year_rollup_path(memory_root, persona_namespace, year)
    existing = load_year_rollup(memory_root, persona_namespace, year)
    children = _month_summaries_for_year(memory_root, persona_namespace, year)
    if not children:
        return str(existing.get("summary", ""))

    if len(children) == 1:
        summary = children[0][1]
    else:
        user_prompt = _merge_summary_prompt("month", children)
        raw = complete_chat(_ROLLUP_SYSTEM, user_prompt, llm_fn=llm_fn)
        data = _parse_json_object(raw)
        summary = str(data.get("summary", "")).strip()

    payload = {
        "level": "year",
        "year": year,
        "session_ids": existing.get("session_ids", []),
        "summary": summary,
    }
    save_rollup(path, payload)
    return summary


def extract_and_store_facts(
    session_summary: str,
    memory_root: Path,
    persona_namespace: str,
    *,
    llm_fn: LlmFn | None = None,
) -> int:
    """Extract durable facts from a session summary and upsert into semantic memory."""
    if not session_summary.strip() or session_summary == "(empty session)":
        return 0

    user_prompt = f"Session summary:\n{session_summary}"
    raw = complete_chat(_FACT_EXTRACTION_SYSTEM, user_prompt, llm_fn=llm_fn)
    data = _parse_json_object(raw)
    facts_raw = data.get("facts", [])
    if not isinstance(facts_raw, list):
        logger.warning("Fact extraction returned non-list facts; skipping")
        return 0

    stored = 0
    for entry in facts_raw:
        if not isinstance(entry, dict):
            continue
        scope_raw = str(entry.get("scope", "persona")).strip().lower()
        scope: MemoryScope = "global" if scope_raw == "global" else "persona"
        name = str(entry.get("name", "notes")).strip() or "notes"
        topic = str(entry.get("topic", "")).strip()
        value = str(entry.get("value", "")).strip()
        if not topic or not value:
            continue
        try:
            upsert_memory_fact(
                memory_root,
                persona_namespace,
                scope=scope,
                name=name,
                topic=topic,
                value=value,
            )
            stored += 1
        except ValueError as exc:
            logger.warning("Skipping invalid fact %r: %s", entry, exc)
    return stored


def _parse_bucket_from_session_dir(session_dir: Path) -> tuple[str, str, str]:
    """Extract year, year-month, year-month-day from session directory path."""
    session_id = session_dir.name
    sessions = session_dir.parent.name
    if sessions != "sessions":
        raise ValueError(f"Unexpected session path layout: {session_dir}")
    year_month_day = session_dir.parent.parent.name
    year_month = session_dir.parent.parent.parent.name
    year = session_dir.parent.parent.parent.parent.name
    return year, year_month, year_month_day


def consolidate_session(
    session_dir: Path,
    memory_root: Path,
    persona_namespace: str,
    *,
    llm_fn: LlmFn | None = None,
) -> bool:
    """Run full consolidation pipeline for one session. Returns True on success."""
    session_path = session_json_path(session_dir)
    session = load_session(session_path)
    if session is None:
        logger.warning("Consolidation skipped — session not found: %s", session_path)
        return False

    try:
        if not session.summary.strip():
            summarize_session(session_dir, llm_fn=llm_fn)
        else:
            logger.debug(
                "Session %r has summary — skipping re-summarize, running rollups",
                session.session_id,
            )

        year, year_month, year_month_day = _parse_bucket_from_session_dir(session_dir)
        rollup_day(
            memory_root,
            persona_namespace,
            year,
            year_month,
            year_month_day,
            llm_fn=llm_fn,
        )
        rollup_month(memory_root, persona_namespace, year, year_month, llm_fn=llm_fn)
        rollup_year(memory_root, persona_namespace, year, llm_fn=llm_fn)

        session = load_session(session_path)
        if session is not None:
            extract_and_store_facts(
                session.summary,
                memory_root,
                persona_namespace,
                llm_fn=llm_fn,
            )
            if session.status != "closed":
                session.status = "closed"
                save_session(session_path, session)

        return True
    except Exception:
        logger.exception(
            "Consolidation failed for session %r — leaving close_pending",
            session.session_id,
        )
        failed = load_session(session_path)
        if failed is not None and failed.status != "close_pending":
            failed.status = "close_pending"
            save_session(session_path, failed)
        return False
