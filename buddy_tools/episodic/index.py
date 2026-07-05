"""Per-persona episodic embedding index for semantic search."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeAlias

import numpy as np

from buddy_tools.episodic.paths import (
    day_rollup_path,
    episodic_root,
    month_rollup_path,
    session_json_path,
    year_rollup_path,
)
from buddy_tools.episodic.provenance import episodic_provenance, parse_session_location, relative_episodic_path
from buddy_tools.episodic.rollup import load_day_rollup, load_month_rollup, load_year_rollup
from buddy_tools.episodic.session import EpisodicSession, find_session_json_files, load_session

logger = logging.getLogger(__name__)

DEFAULT_EMBED_MODEL = "BAAI/bge-small-en-v1.5"
DEFAULT_SEARCH_LIMIT = 10
_SNIPPET_MAX_LEN = 120

_INDEX_DIRNAME = ".index"
_MANIFEST_FILENAME = "manifest.json"
_VECTORS_FILENAME = "vectors.json"

_ENV_EMBED_MODEL = "BUDDY_EPISODIC_EMBED_MODEL"
_ENV_SEARCH_DEFAULT_LIMIT = "BUDDY_EPISODIC_SEARCH_DEFAULT_LIMIT"

EmbedFn: TypeAlias = Callable[[list[str]], list[list[float]]]

_CACHED_MODEL: Any = None


def get_embed_model_name() -> str:
    raw = os.environ.get(_ENV_EMBED_MODEL, "").strip()
    return raw or DEFAULT_EMBED_MODEL


def get_search_default_limit() -> int:
    raw = os.environ.get(_ENV_SEARCH_DEFAULT_LIMIT, "").strip()
    if not raw:
        return DEFAULT_SEARCH_LIMIT
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Invalid %s=%r; using default %d", _ENV_SEARCH_DEFAULT_LIMIT, raw, DEFAULT_SEARCH_LIMIT)
        return DEFAULT_SEARCH_LIMIT
    if value <= 0:
        return DEFAULT_SEARCH_LIMIT
    return value


def index_dir(memory_root: Path, persona_namespace: str) -> Path:
    return episodic_root(memory_root, persona_namespace) / _INDEX_DIRNAME


def _manifest_path(memory_root: Path, persona_namespace: str) -> Path:
    return index_dir(memory_root, persona_namespace) / _MANIFEST_FILENAME


def _vectors_path(memory_root: Path, persona_namespace: str) -> Path:
    return index_dir(memory_root, persona_namespace) / _VECTORS_FILENAME


@dataclass
class EpisodicIndexEntry:
    id: str
    level: str
    text: str
    embedding: list[float]
    session_id: str | None = None
    date: str | None = None
    year: str | None = None
    month: str | None = None
    relative_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "level": self.level,
            "text": self.text,
            "embedding": self.embedding,
            "relative_path": self.relative_path,
        }
        if self.session_id:
            payload["session_id"] = self.session_id
        if self.date:
            payload["date"] = self.date
        if self.year:
            payload["year"] = self.year
        if self.month:
            payload["month"] = self.month
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EpisodicIndexEntry:
        embedding_raw = data.get("embedding", [])
        if not isinstance(embedding_raw, list):
            raise ValueError("embedding must be a list")
        return cls(
            id=str(data["id"]),
            level=str(data["level"]),
            text=str(data.get("text", "")),
            embedding=[float(value) for value in embedding_raw],
            session_id=str(data["session_id"]) if data.get("session_id") else None,
            date=str(data["date"]) if data.get("date") else None,
            year=str(data["year"]) if data.get("year") else None,
            month=str(data["month"]) if data.get("month") else None,
            relative_path=str(data.get("relative_path", "")),
        )


def _get_fastembed_model() -> Any:
    global _CACHED_MODEL
    if _CACHED_MODEL is None:
        from fastembed import TextEmbedding

        _CACHED_MODEL = TextEmbedding(model_name=get_embed_model_name())
    return _CACHED_MODEL


def embed_texts(texts: list[str], *, embed_fn: EmbedFn | None = None) -> list[list[float]]:
    if not texts:
        return []
    if embed_fn is not None:
        return embed_fn(texts)
    model = _get_fastembed_model()
    return [vector.tolist() for vector in model.embed(texts)]


def _snippet(text: str) -> str:
    cleaned = text.strip()
    if not cleaned:
        return ""
    if len(cleaned) <= _SNIPPET_MAX_LEN:
        return cleaned
    return cleaned[: _SNIPPET_MAX_LEN - 3] + "..."


def _session_index_text(session: EpisodicSession) -> str:
    parts = [session.summary.strip()]
    if session.topics:
        parts.append("Topics: " + ", ".join(session.topics))
    return "\n".join(part for part in parts if part)


def _rollup_index_text(level: str, rollup: dict[str, Any]) -> str:
    summary = str(rollup.get("summary", "")).strip()
    if summary:
        return summary
    if level == "year":
        return f"Year {rollup.get('year', '')} episodic rollup"
    if level == "month":
        return f"Month {rollup.get('month', '')} episodic rollup"
    if level == "day":
        return f"Day {rollup.get('date', '')} episodic rollup"
    return ""


def load_index_entries(memory_root: Path, persona_namespace: str) -> list[EpisodicIndexEntry]:
    path = _vectors_path(memory_root, persona_namespace)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        logger.warning("Could not read episodic index %s: %s", path, exc)
        return []
    if not isinstance(data, list):
        return []
    entries: list[EpisodicIndexEntry] = []
    for item in data:
        if isinstance(item, dict):
            try:
                entries.append(EpisodicIndexEntry.from_dict(item))
            except (KeyError, TypeError, ValueError) as exc:
                logger.warning("Skipping invalid index entry: %s", exc)
    return entries


def save_index_entries(
    memory_root: Path,
    persona_namespace: str,
    entries: list[EpisodicIndexEntry],
    *,
    model_name: str | None = None,
) -> None:
    directory = index_dir(memory_root, persona_namespace)
    directory.mkdir(parents=True, exist_ok=True)

    dimension = len(entries[0].embedding) if entries else 0
    manifest = {
        "model": model_name or get_embed_model_name(),
        "dimension": dimension,
        "doc_count": len(entries),
        "last_updated": datetime.now(UTC).replace(microsecond=0).isoformat(),
    }
    _manifest_path(memory_root, persona_namespace).write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    payload = [entry.to_dict() for entry in entries]
    _vectors_path(memory_root, persona_namespace).write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )


def upsert_entries(
    memory_root: Path,
    persona_namespace: str,
    new_entries: list[EpisodicIndexEntry],
    *,
    embed_fn: EmbedFn | None = None,
) -> None:
    if not new_entries:
        return
    existing = {entry.id: entry for entry in load_index_entries(memory_root, persona_namespace)}
    for entry in new_entries:
        existing[entry.id] = entry
    save_index_entries(memory_root, persona_namespace, list(existing.values()))


def remove_entries_for_session(
    memory_root: Path,
    persona_namespace: str,
    session_id: str,
) -> None:
    entries = load_index_entries(memory_root, persona_namespace)
    filtered = [
        entry
        for entry in entries
        if entry.session_id != session_id and entry.id != f"session:{session_id}"
    ]
    if len(filtered) != len(entries):
        save_index_entries(memory_root, persona_namespace, filtered)


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    a = np.array(left, dtype=np.float64)
    b = np.array(right, dtype=np.float64)
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0.0:
        return 0.0
    return float(np.dot(a, b) / denom)


def _build_session_entry(
    session: EpisodicSession,
    session_directory: Path,
    memory_root: Path,
    persona_namespace: str,
    embedding: list[float],
) -> EpisodicIndexEntry | None:
    text = _session_index_text(session)
    if not text:
        return None
    location = parse_session_location(session_directory)
    date = location[2] if location else None
    return EpisodicIndexEntry(
        id=f"session:{session.session_id}",
        level="session",
        text=text,
        embedding=embedding,
        session_id=session.session_id,
        date=date,
        year=location[0] if location else None,
        month=location[1] if location else None,
        relative_path=relative_episodic_path(memory_root, persona_namespace, session_directory),
    )


def _build_rollup_entry(
    *,
    level: str,
    doc_id: str,
    text: str,
    embedding: list[float],
    memory_root: Path,
    persona_namespace: str,
    path: Path,
    year: str | None = None,
    month: str | None = None,
    date: str | None = None,
) -> EpisodicIndexEntry | None:
    if not text.strip():
        return None
    return EpisodicIndexEntry(
        id=doc_id,
        level=level,
        text=text,
        embedding=embedding,
        year=year,
        month=month,
        date=date,
        relative_path=relative_episodic_path(memory_root, persona_namespace, path),
    )


def index_session_summary(
    session_directory: Path,
    memory_root: Path,
    persona_namespace: str,
    *,
    embed_fn: EmbedFn | None = None,
) -> EpisodicIndexEntry | None:
    session = load_session(session_json_path(session_directory))
    if session is None or not session.summary.strip():
        return None
    embedding = embed_texts([_session_index_text(session)], embed_fn=embed_fn)[0]
    return _build_session_entry(session, session_directory, memory_root, persona_namespace, embedding)


def index_rollup(
    level: str,
    memory_root: Path,
    persona_namespace: str,
    *,
    year: str,
    month: str | None = None,
    date: str | None = None,
    embed_fn: EmbedFn | None = None,
) -> EpisodicIndexEntry | None:
    if level == "year":
        path = year_rollup_path(memory_root, persona_namespace, year)
        rollup = load_year_rollup(memory_root, persona_namespace, year)
        doc_id = f"year:{year}"
        text = _rollup_index_text(level, rollup)
        embedding = embed_texts([text], embed_fn=embed_fn)[0] if text.strip() else []
        if not embedding:
            return None
        return _build_rollup_entry(
            level=level,
            doc_id=doc_id,
            text=text,
            embedding=embedding,
            memory_root=memory_root,
            persona_namespace=persona_namespace,
            path=path,
            year=year,
        )

    if level == "month":
        if not month:
            raise ValueError("month is required for month rollup indexing")
        path = month_rollup_path(memory_root, persona_namespace, year, month)
        rollup = load_month_rollup(memory_root, persona_namespace, year, month)
        doc_id = f"month:{month}"
        text = _rollup_index_text(level, rollup)
        embedding = embed_texts([text], embed_fn=embed_fn)[0] if text.strip() else []
        if not embedding:
            return None
        return _build_rollup_entry(
            level=level,
            doc_id=doc_id,
            text=text,
            embedding=embedding,
            memory_root=memory_root,
            persona_namespace=persona_namespace,
            path=path,
            year=year,
            month=month,
        )

    if level == "day":
        if not month or not date:
            raise ValueError("month and date are required for day rollup indexing")
        path = day_rollup_path(memory_root, persona_namespace, year, month, date)
        rollup = load_day_rollup(memory_root, persona_namespace, year, month, date)
        doc_id = f"day:{date}"
        text = _rollup_index_text(level, rollup)
        embedding = embed_texts([text], embed_fn=embed_fn)[0] if text.strip() else []
        if not embedding:
            return None
        return _build_rollup_entry(
            level=level,
            doc_id=doc_id,
            text=text,
            embedding=embedding,
            memory_root=memory_root,
            persona_namespace=persona_namespace,
            path=path,
            year=year,
            month=month,
            date=date,
        )

    raise ValueError(f"unsupported rollup level: {level!r}")


def index_consolidated_session(
    session_directory: Path,
    memory_root: Path,
    persona_namespace: str,
    *,
    embed_fn: EmbedFn | None = None,
) -> None:
    """Index session and rollup summaries after consolidation. Fail-soft on errors."""
    try:
        session = load_session(session_json_path(session_directory))
        if session is None:
            return

        entries: list[EpisodicIndexEntry] = []
        session_entry = index_session_summary(
            session_directory,
            memory_root,
            persona_namespace,
            embed_fn=embed_fn,
        )
        if session_entry is not None:
            entries.append(session_entry)

        year, year_month, year_month_day = _parse_bucket_from_session_dir(session_directory)
        for level, kwargs in (
            ("day", {"year": year, "month": year_month, "date": year_month_day}),
            ("month", {"year": year, "month": year_month}),
            ("year", {"year": year}),
        ):
            rollup_entry = index_rollup(
                level,
                memory_root,
                persona_namespace,
                embed_fn=embed_fn,
                **kwargs,  # type: ignore[arg-type]
            )
            if rollup_entry is not None:
                entries.append(rollup_entry)

        if entries:
            upsert_entries(memory_root, persona_namespace, entries, embed_fn=embed_fn)
    except Exception as exc:
        logger.warning(
            "Episodic index update failed for %s: %s",
            session_directory,
            exc,
            exc_info=True,
        )


def _parse_bucket_from_session_dir(session_dir: Path) -> tuple[str, str, str]:
    sessions = session_dir.parent.name
    if sessions != "sessions":
        raise ValueError(f"Unexpected session path layout: {session_dir}")
    year_month_day = session_dir.parent.parent.name
    year_month = session_dir.parent.parent.parent.name
    year = session_dir.parent.parent.parent.parent.name
    return year, year_month, year_month_day


def search_index(
    memory_root: Path,
    persona_namespace: str,
    query: str,
    *,
    limit: int | None = None,
    embed_fn: EmbedFn | None = None,
) -> list[dict[str, Any]]:
    query_clean = query.strip()
    if not query_clean:
        raise ValueError("query is required")

    effective_limit = limit if limit is not None else get_search_default_limit()
    if effective_limit < 1:
        raise ValueError("limit must be >= 1")

    entries = load_index_entries(memory_root, persona_namespace)
    if not entries:
        return []

    query_embedding = embed_texts([query_clean], embed_fn=embed_fn)[0]
    scored: list[tuple[float, EpisodicIndexEntry]] = []
    for entry in entries:
        if len(entry.embedding) != len(query_embedding):
            continue
        score = _cosine_similarity(query_embedding, entry.embedding)
        scored.append((score, entry))

    scored.sort(key=lambda item: item[0], reverse=True)
    hits: list[dict[str, Any]] = []
    for score, entry in scored[:effective_limit]:
        path = episodic_root(memory_root, persona_namespace) / entry.relative_path
        provenance = episodic_provenance(
            memory_root,
            persona_namespace,
            path,
            session_id=entry.session_id,
        )
        hit: dict[str, Any] = {
            "score": round(score, 4),
            "level": entry.level,
            "snippet": _snippet(entry.text),
            "provenance": provenance,
        }
        if entry.session_id:
            hit["session_id"] = entry.session_id
        if entry.date:
            hit["date"] = entry.date
        if entry.year:
            hit["year"] = entry.year
        if entry.month:
            hit["month"] = entry.month
        hits.append(hit)
    return hits


def rebuild_episodic_index(
    memory_root: Path,
    persona_namespace: str,
    *,
    embed_fn: EmbedFn | None = None,
) -> int:
    """Rebuild the full episodic index from consolidated summaries. Returns doc count."""
    tree = episodic_root(memory_root, persona_namespace)
    texts: list[str] = []
    builders: list[Callable[[list[float]], EpisodicIndexEntry | None]] = []

    for session_path in find_session_json_files(tree):
        session = load_session(session_path)
        if session is None or not session.summary.strip():
            continue
        session_directory = session_path.parent
        text = _session_index_text(session)

        def make_session_builder(
            sess: EpisodicSession = session,
            sess_dir: Path = session_directory,
            sess_text: str = text,
        ) -> Callable[[list[float]], EpisodicIndexEntry | None]:
            def build(embedding: list[float]) -> EpisodicIndexEntry | None:
                return _build_session_entry(sess, sess_dir, memory_root, persona_namespace, embedding)

            return build

        texts.append(text)
        builders.append(make_session_builder())

    for year_path in sorted(tree.glob("*/year.json")):
        year = year_path.parent.name
        rollup = load_year_rollup(memory_root, persona_namespace, year)
        text = _rollup_index_text("year", rollup)
        if not text.strip():
            continue

        def make_year_builder(
            yr: str = year,
            yr_path: Path = year_path,
            yr_text: str = text,
        ) -> Callable[[list[float]], EpisodicIndexEntry | None]:
            def build(embedding: list[float]) -> EpisodicIndexEntry | None:
                return _build_rollup_entry(
                    level="year",
                    doc_id=f"year:{yr}",
                    text=yr_text,
                    embedding=embedding,
                    memory_root=memory_root,
                    persona_namespace=persona_namespace,
                    path=yr_path,
                    year=yr,
                )

            return build

        texts.append(text)
        builders.append(make_year_builder())

    for month_path in sorted(tree.glob("*/*/month.json")):
        parts = month_path.relative_to(tree).parts
        if len(parts) != 3:
            continue
        year, year_month = parts[0], parts[1]
        rollup = load_month_rollup(memory_root, persona_namespace, year, year_month)
        text = _rollup_index_text("month", rollup)
        if not text.strip():
            continue

        def make_month_builder(
            yr: str = year,
            yr_month: str = year_month,
            month_path_local: Path = month_path,
            month_text: str = text,
        ) -> Callable[[list[float]], EpisodicIndexEntry | None]:
            def build(embedding: list[float]) -> EpisodicIndexEntry | None:
                return _build_rollup_entry(
                    level="month",
                    doc_id=f"month:{yr_month}",
                    text=month_text,
                    embedding=embedding,
                    memory_root=memory_root,
                    persona_namespace=persona_namespace,
                    path=month_path_local,
                    year=yr,
                    month=yr_month,
                )

            return build

        texts.append(text)
        builders.append(make_month_builder())

    for day_path in sorted(tree.glob("*/*/*/day.json")):
        parts = day_path.relative_to(tree).parts
        if len(parts) != 4:
            continue
        year, year_month, year_month_day = parts[0], parts[1], parts[2]
        rollup = load_day_rollup(memory_root, persona_namespace, year, year_month, year_month_day)
        text = _rollup_index_text("day", rollup)
        if not text.strip():
            continue

        def make_day_builder(
            yr: str = year,
            yr_month: str = year_month,
            yr_month_day: str = year_month_day,
            day_path_local: Path = day_path,
            day_text: str = text,
        ) -> Callable[[list[float]], EpisodicIndexEntry | None]:
            def build(embedding: list[float]) -> EpisodicIndexEntry | None:
                return _build_rollup_entry(
                    level="day",
                    doc_id=f"day:{yr_month_day}",
                    text=day_text,
                    embedding=embedding,
                    memory_root=memory_root,
                    persona_namespace=persona_namespace,
                    path=day_path_local,
                    year=yr,
                    month=yr_month,
                    date=yr_month_day,
                )

            return build

        texts.append(text)
        builders.append(make_day_builder())

    if not texts:
        save_index_entries(memory_root, persona_namespace, [])
        return 0

    embeddings = embed_texts(texts, embed_fn=embed_fn)
    entries: list[EpisodicIndexEntry] = []
    for builder, embedding in zip(builders, embeddings, strict=True):
        entry = builder(embedding)
        if entry is not None:
            entries.append(entry)

    save_index_entries(memory_root, persona_namespace, entries)
    return len(entries)


def _cli_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rebuild episodic semantic search index")
    parser.add_argument("--memory-root", type=Path, required=True)
    parser.add_argument("--persona", required=True, help="Persona memory namespace")
    parser.add_argument("--rebuild", action="store_true", help="Rebuild index from summaries")
    args = parser.parse_args(argv)

    if not args.rebuild:
        parser.print_help()
        return 1

    count = rebuild_episodic_index(args.memory_root.resolve(), args.persona.strip())
    print(f"Rebuilt episodic index: {count} documents")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli_main())
