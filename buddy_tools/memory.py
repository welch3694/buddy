"""Persistent memory tools backed by markdown files in namespaced directories."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Literal

from openai.types.realtime import RealtimeFunctionTool

from buddy_tools.result import ToolExecutionResult
from buddy_tools.tool_logging import safe_tool_context, tool_error

logger = logging.getLogger(__name__)

_SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
GLOBAL_NAMESPACE = "global"
MemoryScope = Literal["global", "persona"]
_SCOPE_PROPERTY = {
    "type": "string",
    "enum": ["global", "persona"],
    "description": (
        "Where to store or read the document: global for user facts shared across "
        "all personas, persona for role-specific state for the active personality"
    ),
}

MEMORY_TOOL_DEFINITIONS: list[RealtimeFunctionTool] = [
    RealtimeFunctionTool(
        type="function",
        name="list_memory",
        description="List available persistent memory documents (without .md extension) in global and persona scopes.",
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
                },
                "scope": _SCOPE_PROPERTY,
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
                "scope": _SCOPE_PROPERTY,
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
                "scope": _SCOPE_PROPERTY,
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
                "scope": _SCOPE_PROPERTY,
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


def _sanitize_namespace(namespace: str) -> str:
    cleaned = namespace.strip().lower().replace(" ", "_")
    cleaned = re.sub(r"[^a-z0-9_-]", "", cleaned)
    if not cleaned or not _SAFE_NAME.match(cleaned):
        raise ValueError(f"Invalid memory namespace: {namespace!r}")
    return cleaned


def global_memory_dir(memory_root: Path) -> Path:
    return memory_root.resolve() / GLOBAL_NAMESPACE


def persona_memory_dir(memory_root: Path, persona_namespace: str) -> Path:
    return memory_root.resolve() / _sanitize_namespace(persona_namespace)


def _parse_scope(args: dict[str, Any]) -> MemoryScope:
    raw = str(args.get("scope", "persona")).strip().lower()
    if raw in ("global", "persona"):
        return raw  # type: ignore[return-value]
    raise ValueError(f"Invalid memory scope: {raw!r} (use global or persona)")


def _scope_dir(memory_root: Path, persona_namespace: str, scope: MemoryScope) -> Path:
    if scope == "global":
        return global_memory_dir(memory_root)
    return persona_memory_dir(memory_root, persona_namespace)


def _resolve_path(memory_root: Path, persona_namespace: str, scope: MemoryScope, name: str) -> Path:
    memory_dir = _scope_dir(memory_root, persona_namespace, scope)
    memory_root_resolved = memory_root.resolve()
    path = (memory_dir / f"{_sanitize_name(name)}.md").resolve()
    if path.parent != memory_dir.resolve() or memory_root_resolved not in path.parents:
        raise ValueError(f"Invalid memory document name: {name!r}")
    return path


def migrate_legacy_memory(memory_root: Path) -> bool:
    """Move flat memory/notes.md into memory/global/notes.md if present."""
    memory_root = memory_root.resolve()
    legacy_notes = memory_root / "notes.md"
    if not legacy_notes.is_file():
        return False

    target_dir = global_memory_dir(memory_root)
    target_dir.mkdir(parents=True, exist_ok=True)
    target_notes = target_dir / "notes.md"
    if not target_notes.exists():
        target_notes.write_text(legacy_notes.read_text(encoding="utf-8"), encoding="utf-8")
        logger.info("Migrated legacy memory to %s", target_notes)
    legacy_notes.unlink()
    return True


def _load_dir_summary(memory_dir: Path, heading: str) -> str | None:
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
        parts.append(f"#### {path.stem}\n{text}")
    if not parts:
        return None
    return f"### {heading}\n" + "\n\n".join(parts)


def load_memory_summary(
    memory_root: Path,
    persona_namespace: str,
    max_chars: int = 4000,
) -> str:
    """Load global and active persona memory for injection into the system prompt."""
    memory_root.mkdir(parents=True, exist_ok=True)
    migrate_legacy_memory(memory_root)

    sections: list[str] = []
    global_section = _load_dir_summary(global_memory_dir(memory_root), "Global (all personas)")
    if global_section:
        sections.append(global_section)

    persona_section = _load_dir_summary(
        persona_memory_dir(memory_root, persona_namespace),
        f"Persona ({_sanitize_namespace(persona_namespace)})",
    )
    if persona_section:
        sections.append(persona_section)

    if not sections:
        return "(no memory saved yet)"
    summary = "\n\n".join(sections)
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
        "You have persistent memory stored as markdown files in two scopes:\n"
        "- global: user facts any persona should know (name, preferences, household details)\n"
        "- persona: role-specific state for the active personality only\n"
        "Use the memory tools when needed:\n"
        "- update_memory: set or correct one fact (preferred for remember / changed my mind)\n"
        "- append_memory: only for new facts that do not contradict existing memory\n"
        "- read_memory / list_memory: read before answering about stored context\n"
        "- write_memory: replace a whole document only when reorganizing many facts\n"
        "Pass scope=global for shared user facts; omit scope or use scope=persona for persona-only notes.\n"
        "Each topic should have one current value in memory. Never store conflicting lines "
        "for the same topic (e.g. both blue and red as favorite color).\n"
        "Keep memory concise (bullet points like '- Favorite color: red').\n"
        "After saving memory, confirm briefly in spoken language without mentioning tools or files."
    )


def execute_memory_tool(
    memory_root: Path,
    persona_namespace: str,
    tool_name: str,
    args: dict[str, Any],
) -> ToolExecutionResult:
    memory_root.mkdir(parents=True, exist_ok=True)
    migrate_legacy_memory(memory_root)

    if tool_name == "list_memory":
        global_names = sorted(p.stem for p in global_memory_dir(memory_root).glob("*.md"))
        persona_names = sorted(
            p.stem for p in persona_memory_dir(memory_root, persona_namespace).glob("*.md")
        )
        return ToolExecutionResult(
            output=json.dumps({"global": global_names, "persona": persona_names})
        )

    scope = _parse_scope(args)

    if tool_name == "read_memory":
        path = _resolve_path(memory_root, persona_namespace, scope, str(args.get("name", "")))
        if not path.exists():
            return ToolExecutionResult(output="")
        return ToolExecutionResult(output=path.read_text(encoding="utf-8"))

    if tool_name == "update_memory":
        path = _resolve_path(memory_root, persona_namespace, scope, str(args.get("name", "")))
        topic = str(args.get("topic", "")).strip()
        value = str(args.get("value", "")).strip()
        if not topic:
            return tool_error(tool_name, "topic is empty", context=safe_tool_context(args))
        if not value:
            return tool_error(tool_name, "value is empty", context=safe_tool_context(args))
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = path.read_text(encoding="utf-8") if path.exists() else "# Notes\n"
        updated = _upsert_fact_line(existing, topic, value)
        path.write_text(updated, encoding="utf-8")
        logger.info("Memory update [%s]: %s topic=%r value=%r", scope, path.name, topic, value)
        return ToolExecutionResult(output=f"Updated {topic} in {scope}/{path.stem}")

    if tool_name == "write_memory":
        path = _resolve_path(memory_root, persona_namespace, scope, str(args.get("name", "")))
        content = str(args.get("content", ""))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content.rstrip() + "\n", encoding="utf-8")
        logger.info("Memory write [%s]: %s (%d chars)", scope, path.name, len(content))
        return ToolExecutionResult(output=f"Saved {scope}/{path.stem}")

    if tool_name == "append_memory":
        path = _resolve_path(memory_root, persona_namespace, scope, str(args.get("name", "")))
        content = str(args.get("content", "")).strip()
        if not content:
            return tool_error(tool_name, "content is empty", context=safe_tool_context(args))
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            existing = path.read_text(encoding="utf-8").rstrip()
            new_text = f"{existing}\n{content}\n"
        else:
            new_text = f"{content}\n"
        path.write_text(new_text, encoding="utf-8")
        logger.info("Memory append [%s]: %s", scope, path.name)
        return ToolExecutionResult(output=f"Appended to {scope}/{path.stem}")

    return tool_error(tool_name, f"unknown memory tool {tool_name!r}")
