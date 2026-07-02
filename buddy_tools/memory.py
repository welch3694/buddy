"""Persistent memory tools backed by markdown files in a memory directory."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from openai.types.realtime import RealtimeFunctionTool

from buddy_tools.result import ToolExecutionResult

logger = logging.getLogger(__name__)

_SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

MEMORY_TOOL_DEFINITIONS: list[RealtimeFunctionTool] = [
    RealtimeFunctionTool(
        type="function",
        name="list_memory",
        description="List available persistent memory documents (without .md extension).",
        parameters={
            "type": "object",
            "properties": {},
        },
    ),
    RealtimeFunctionTool(
        type="function",
        name="read_memory",
        description="Read a persistent memory document by name.",
        parameters={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Document name, e.g. notes or user_profile",
                }
            },
            "required": ["name"],
        },
    ),
    RealtimeFunctionTool(
        type="function",
        name="update_memory",
        description=(
            "Set or replace one fact in a memory document. Use when the user states or "
            "corrects a specific fact (e.g. favorite color, name, preference). Replaces any "
            "existing line about the same topic instead of adding a duplicate."
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Document name, usually notes",
                },
                "topic": {
                    "type": "string",
                    "description": "Short label for the fact, e.g. favorite color",
                },
                "value": {
                    "type": "string",
                    "description": "The current correct value for that topic",
                },
            },
            "required": ["name", "topic", "value"],
        },
    ),
    RealtimeFunctionTool(
        type="function",
        name="write_memory",
        description=(
            "Replace an entire memory document. Use only when reorganizing many facts at once, "
            "not for a single correction (use update_memory instead)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "content": {"type": "string", "description": "Full markdown content"},
            },
            "required": ["name", "content"],
        },
    ),
    RealtimeFunctionTool(
        type="function",
        name="append_memory",
        description=(
            "Add a genuinely new fact that does not replace or contradict anything already stored. "
            "Do not use for corrections or changes of mind; use update_memory instead."
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "content": {"type": "string", "description": "Text to append"},
            },
            "required": ["name", "content"],
        },
    ),
]

MEMORY_TOOL_NAMES = frozenset(tool.name for tool in MEMORY_TOOL_DEFINITIONS)


def _sanitize_name(name: str) -> str:
    cleaned = name.strip().lower().replace(" ", "_")
    cleaned = re.sub(r"[^a-z0-9_-]", "", cleaned)
    if not cleaned or not _SAFE_NAME.match(cleaned):
        raise ValueError(f"Invalid memory document name: {name!r}")
    return cleaned


def _resolve_path(memory_dir: Path, name: str) -> Path:
    memory_root = memory_dir.resolve()
    path = (memory_root / f"{_sanitize_name(name)}.md").resolve()
    if path.parent != memory_root:
        raise ValueError(f"Invalid memory document name: {name!r}")
    return path


def load_memory_summary(memory_dir: Path, max_chars: int = 4000) -> str:
    """Load all memory files for injection into the system prompt."""
    memory_dir.mkdir(parents=True, exist_ok=True)
    parts: list[str] = []
    for path in sorted(memory_dir.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            logger.warning("Could not read %s: %s", path, exc)
            continue
        if not text:
            continue
        parts.append(f"### {path.stem}\n{text}")
    if not parts:
        return "(no memory saved yet)"
    summary = "\n\n".join(parts)
    if len(summary) > max_chars:
        return summary[: max_chars - 3] + "..."
    return summary


def _normalize_topic(topic: str) -> str:
    return re.sub(r"\s+", " ", topic.strip().lower())


def _line_matches_topic(line: str, topic: str) -> bool:
    stripped = line.strip().lstrip("-*").strip()
    if ":" not in stripped:
        return False
    label = stripped.split(":", 1)[0]
    topic_norm = _normalize_topic(topic)
    label_norm = _normalize_topic(label)
    return topic_norm == label_norm or topic_norm in label_norm or label_norm in topic_norm


def _upsert_fact_line(content: str, topic: str, value: str) -> str:
    value = value.strip()
    topic_clean = topic.strip()
    new_line = f"- {topic_clean[0].upper() + topic_clean[1:] if topic_clean else topic_clean}: {value}"

    lines = content.splitlines()
    replaced = False
    for index, line in enumerate(lines):
        if _line_matches_topic(line, topic):
            lines[index] = new_line
            replaced = True
            break

    if not replaced:
        if lines and lines[-1].strip():
            lines.append(new_line)
        else:
            while lines and not lines[-1].strip():
                lines.pop()
            lines.append(new_line)

    return "\n".join(lines).rstrip() + "\n"


def build_memory_instructions() -> str:
    return (
        "You have persistent memory stored as markdown files. Use the memory tools when needed:\n"
        "- update_memory: set or correct one fact (preferred for remember / changed my mind)\n"
        "- append_memory: only for new facts that do not contradict existing memory\n"
        "- read_memory: read before answering about stored context\n"
        "- write_memory: replace a whole document only when reorganizing many facts\n"
        "- list_memory: see which documents exist\n"
        "Each topic should have one current value in memory. Never store conflicting lines "
        "for the same topic (e.g. both blue and red as favorite color).\n"
        "Keep memory concise (bullet points like '- Favorite color: red').\n"
        "After saving memory, confirm briefly in spoken language without mentioning tools or files."
    )


def execute_memory_tool(memory_dir: Path, tool_name: str, args: dict[str, Any]) -> ToolExecutionResult:
    memory_dir.mkdir(parents=True, exist_ok=True)

    if tool_name == "list_memory":
        names = sorted(p.stem for p in memory_dir.glob("*.md"))
        return ToolExecutionResult(output=json.dumps({"documents": names}))

    if tool_name == "read_memory":
        path = _resolve_path(memory_dir, str(args.get("name", "")))
        if not path.exists():
            return ToolExecutionResult(output="")
        return ToolExecutionResult(output=path.read_text(encoding="utf-8"))

    if tool_name == "update_memory":
        path = _resolve_path(memory_dir, str(args.get("name", "")))
        topic = str(args.get("topic", "")).strip()
        value = str(args.get("value", "")).strip()
        if not topic:
            return ToolExecutionResult(output="Error: topic is empty")
        if not value:
            return ToolExecutionResult(output="Error: value is empty")
        existing = path.read_text(encoding="utf-8") if path.exists() else "# Notes\n"
        updated = _upsert_fact_line(existing, topic, value)
        path.write_text(updated, encoding="utf-8")
        logger.info("Memory update: %s topic=%r value=%r", path.name, topic, value)
        return ToolExecutionResult(output=f"Updated {topic} in {path.stem}")

    if tool_name == "write_memory":
        path = _resolve_path(memory_dir, str(args.get("name", "")))
        content = str(args.get("content", ""))
        path.write_text(content.rstrip() + "\n", encoding="utf-8")
        logger.info("Memory write: %s (%d chars)", path.name, len(content))
        return ToolExecutionResult(output=f"Saved {path.stem}")

    if tool_name == "append_memory":
        path = _resolve_path(memory_dir, str(args.get("name", "")))
        content = str(args.get("content", "")).strip()
        if not content:
            return ToolExecutionResult(output="Error: content is empty")
        if path.exists():
            existing = path.read_text(encoding="utf-8").rstrip()
            new_text = f"{existing}\n{content}\n"
        else:
            new_text = f"{content}\n"
        path.write_text(new_text, encoding="utf-8")
        logger.info("Memory append: %s", path.name)
        return ToolExecutionResult(output=f"Appended to {path.stem}")

    return ToolExecutionResult(output=f"Error: unknown memory tool {tool_name!r}")
