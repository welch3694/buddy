"""Regenerate episodic summaries from stored turns or child rollups.

Usage (CLI)::

    python -m buddy_tools.episodic.regenerate --memory-root ./memory --persona buddy --session-id 20260705T140000-abc12345

    python -m buddy_tools.episodic.regenerate --memory-root ./memory --persona buddy --day 2026-07-05

Module API::

    from buddy_tools.episodic.regenerate import regenerate_session, regenerate_day

    regenerate_session(session_dir, llm_fn=my_mock)
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from buddy_tools.episodic.consolidation import (
    consolidate_session,
    rollup_day,
    rollup_month,
    rollup_year,
    summarize_session,
)
from buddy_tools.episodic.paths import (
    day_dir,
    episodic_root,
    session_dir,
    sessions_dir,
)
from buddy_tools.episodic.session import load_session
from buddy_tools.infra.llm_client import LlmFn

logger = logging.getLogger(__name__)


def find_session_directory(
    memory_root: Path,
    persona_namespace: str,
    session_id: str,
) -> Path | None:
    tree = episodic_root(memory_root, persona_namespace)
    for path in tree.rglob("session.json"):
        session = load_session(path)
        if session is not None and session.session_id == session_id:
            return path.parent
    return None


def regenerate_session(
    session_directory: Path,
    memory_root: Path,
    persona_namespace: str,
    *,
    llm_fn: LlmFn | None = None,
    full_pipeline: bool = True,
) -> bool:
    """Re-summarize a session from turns.jsonl and optionally run rollups + facts."""
    if full_pipeline:
        return consolidate_session(
            session_directory,
            memory_root,
            persona_namespace,
            llm_fn=llm_fn,
        )
    summarize_session(session_directory, llm_fn=llm_fn)
    return True


def regenerate_day(
    memory_root: Path,
    persona_namespace: str,
    year: str,
    year_month: str,
    year_month_day: str,
    *,
    llm_fn: LlmFn | None = None,
    resummarize_sessions: bool = False,
) -> str:
    """Rebuild day rollup; optionally re-summarize each session in the day from turns."""
    if resummarize_sessions:
        base = sessions_dir(memory_root, persona_namespace, year, year_month, year_month_day)
        if base.is_dir():
            for child in sorted(base.iterdir()):
                if child.is_dir():
                    summarize_session(child, llm_fn=llm_fn)
    return rollup_day(
        memory_root,
        persona_namespace,
        year,
        year_month,
        year_month_day,
        llm_fn=llm_fn,
    )


def regenerate_month(
    memory_root: Path,
    persona_namespace: str,
    year: str,
    year_month: str,
    *,
    llm_fn: LlmFn | None = None,
) -> str:
    return rollup_month(memory_root, persona_namespace, year, year_month, llm_fn=llm_fn)


def regenerate_year(
    memory_root: Path,
    persona_namespace: str,
    year: str,
    *,
    llm_fn: LlmFn | None = None,
) -> str:
    return rollup_year(memory_root, persona_namespace, year, llm_fn=llm_fn)


def _parse_day_key(day_key: str) -> tuple[str, str, str]:
    parts = day_key.strip().split("-")
    if len(parts) != 3:
        raise ValueError(f"Day key must be YYYY-MM-DD, got {day_key!r}")
    year, month, day = parts
    year_month = f"{year}-{month}"
    year_month_day = f"{year_month}-{day}"
    return year, year_month, year_month_day


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Regenerate episodic memory summaries")
    parser.add_argument("--memory-root", type=Path, required=True)
    parser.add_argument("--persona", required=True, help="Persona memory namespace")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--session-id", help="Session id to regenerate")
    group.add_argument("--day", help="Day key YYYY-MM-DD to regenerate rollup")
    group.add_argument("--month", help="Month key YYYY-MM to regenerate rollup")
    group.add_argument("--year", help="Year YYYY to regenerate rollup")
    parser.add_argument(
        "--resummarize-sessions",
        action="store_true",
        help="When using --day, re-read turns.jsonl for each session first",
    )
    args = parser.parse_args(argv)

    memory_root = args.memory_root.resolve()
    persona = args.persona.strip()

    if args.session_id:
        session_directory = find_session_directory(memory_root, persona, args.session_id)
        if session_directory is None:
            print(f"Session not found: {args.session_id}", file=sys.stderr)
            return 1
        ok = regenerate_session(session_directory, memory_root, persona)
        print(f"Regenerated session {args.session_id}: {'ok' if ok else 'failed'}")
        return 0 if ok else 1

    if args.day:
        year, year_month, year_month_day = _parse_day_key(args.day)
        summary = regenerate_day(
            memory_root,
            persona,
            year,
            year_month,
            year_month_day,
            resummarize_sessions=args.resummarize_sessions,
        )
        print(f"Day {args.day} summary ({len(summary)} chars)")
        return 0

    if args.month:
        parts = args.month.strip().split("-")
        if len(parts) != 2:
            print(f"Invalid month key: {args.month}", file=sys.stderr)
            return 1
        year, _ = parts
        summary = regenerate_month(memory_root, persona, year, args.month)
        print(f"Month {args.month} summary ({len(summary)} chars)")
        return 0

    if args.year:
        summary = regenerate_year(memory_root, persona, args.year.strip())
        print(f"Year {args.year} summary ({len(summary)} chars)")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
