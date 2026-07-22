"""Personality skills: Agent Skills layout, runtime state, and LLM tools."""

from __future__ import annotations

import json
import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from openai.types.realtime import RealtimeFunctionTool

from buddy_tools.memory import persona_memory_dir
from buddy_tools.personality import PersonalityProfile, get_active_personality, get_personality
from buddy_tools.core.consolidate import ActionSpec, build_action_tool, resolve_action_args
from buddy_tools.core.groups import ToolGroup
from buddy_tools.core.result import ToolExecutionResult
from buddy_tools.pulse import (
    clear_pulse_state,
    init_pulse_state_from_skill,
    load_pulse_state,
    save_pulse_state,
    start_pulse_worker,
    stop_pulse_worker,
)
from buddy_tools.pulse.config_merge import apply_pulse_config
from buddy_tools.pulse.schema import SessionValidationError, parse_session_config
from buddy_tools.pulse.state import PulseState
from buddy_tools.pulse.template import render_session_template
from buddy_tools.timers import cancel_timers_for_skill
from buddy_tools.core.tool_logging import safe_tool_context, tool_error

logger = logging.getLogger(__name__)

_SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_SKILL_FILENAME = "SKILL.md"
_SKILL_STATE_FILENAME = "skill_state.json"
_RESOURCE_DIRS = frozenset({"references", "scripts", "assets"})
SkillStatus = Literal["in_progress", "paused"]
SkillType = Literal["checklist", "generic", "pulse"]
SkillSource = Literal["builtin", "shared", "personality"]

_SKILL_NAME_PROPERTY = {
    "type": "string",
    "description": "Skill name/id, e.g. equipment-setup or director-flow (lowercase letters, digits, hyphens)",
}
_SKILL_NAME_REF_PROPERTY = {
    "type": "string",
    "description": (
        "Skill name to target; for read_file omit to read from the active skill only"
    ),
}
_PATH_PROPERTY = {
    "type": "string",
    "description": "Relative path under references/, scripts/, or assets/, e.g. references/checklist.md",
}
_DESCRIPTION_PROPERTY = {
    "type": "string",
    "description": "When-to-use description for skill discovery",
}
_BODY_PROPERTY = {
    "type": "string",
    "description": "Markdown body after frontmatter, or a full SKILL.md including --- frontmatter",
}
_SCOPE_PROPERTY = {
    "type": "string",
    "enum": ["persona", "shared"],
    "description": (
        "persona (default) or shared for cross-persona placement; for update/delete "
        "defaults to highest-precedence match"
    ),
}
_PERSONALITY_ID_PROPERTY = {
    "type": "string",
    "description": (
        "Target personality for persona scope (default active); for shared scope, "
        "limits visibility to this personality when set"
    ),
}
_SKILL_TYPE_PROPERTY = {
    "type": "string",
    "enum": ["checklist", "generic", "pulse"],
    "description": "checklist requires ## Steps with ### headings; pulse uses references/session.yaml",
}

SKILL_ACTIONS: tuple[ActionSpec, ...] = (
    ActionSpec(
        action="list",
        legacy_name="list_skills",
    ),
    ActionSpec(
        action="start",
        legacy_name="start_skill",
        required=("name",),
        properties={"name": _SKILL_NAME_PROPERTY},
    ),
    ActionSpec(action="status", legacy_name="skill_status"),
    ActionSpec(
        action="advance",
        legacy_name="advance_skill",
        properties={
            "skip": {
                "type": "boolean",
                "description": "Skip the current step instead of completing it",
            },
            "reason": {
                "type": "string",
                "description": "Optional reason when skipping a step",
            },
        },
    ),
    ActionSpec(action="pause", legacy_name="pause_skill"),
    ActionSpec(action="cancel", legacy_name="cancel_skill"),
    ActionSpec(
        action="create",
        legacy_name="create_skill",
        required=("name", "description", "body"),
        properties={
            "name": _SKILL_NAME_PROPERTY,
            "description": _DESCRIPTION_PROPERTY,
            "body": _BODY_PROPERTY,
            "scope": _SCOPE_PROPERTY,
            "personality_id": _PERSONALITY_ID_PROPERTY,
            "skill_type": _SKILL_TYPE_PROPERTY,
        },
    ),
    ActionSpec(
        action="update",
        legacy_name="update_skill",
        required=("name",),
        properties={
            "name": _SKILL_NAME_PROPERTY,
            "description": _DESCRIPTION_PROPERTY,
            "body": _BODY_PROPERTY,
            "scope": _SCOPE_PROPERTY,
            "personality_id": _PERSONALITY_ID_PROPERTY,
            "skill_type": _SKILL_TYPE_PROPERTY,
        },
    ),
    ActionSpec(
        action="delete",
        legacy_name="delete_skill",
        required=("name",),
        properties={
            "name": _SKILL_NAME_PROPERTY,
            "scope": _SCOPE_PROPERTY,
            "personality_id": _PERSONALITY_ID_PROPERTY,
        },
    ),
    ActionSpec(
        action="read_file",
        legacy_name="read_skill_file",
        required=("path",),
        properties={
            "path": _PATH_PROPERTY,
            "skill_name": _SKILL_NAME_REF_PROPERTY,
        },
    ),
    ActionSpec(
        action="write_file",
        legacy_name="write_skill_file",
        required=("skill_name", "path", "content"),
        properties={
            "skill_name": _SKILL_NAME_REF_PROPERTY,
            "path": _PATH_PROPERTY,
            "content": {"type": "string", "description": "Full file content to write"},
        },
    ),
    ActionSpec(
        action="update_pulse_config",
        legacy_name="update_pulse_config",
        required=("skill_name", "params"),
        properties={
            "skill_name": _SKILL_NAME_REF_PROPERTY,
            "params": {
                "type": "object",
                "description": (
                    "Keys: camera_switch_interval_s, cameras, conversation_min_silence_s, "
                    "min_speak_interval_s, tick_interval_s, mandatory_cue_max_defer_s"
                ),
            },
        },
    ),
)

SKILL_TOOL_DEFINITION: RealtimeFunctionTool = build_action_tool(
    name="skill",
    description=(
        "Skill workflow operations: built-in, shared, and persona-scoped guided workflows. "
        "Use action=list to discover skills, action=start/status/advance/pause/cancel to run "
        "a checklist or pulse session, action=create/update/delete to manage persona or shared "
        "skills, action=read_file/write_file for skill resources, or action=update_pulse_config "
        "to tune a pulse skill's session.yaml."
    ),
    actions=SKILL_ACTIONS,
)

SKILL_TOOL_DEFINITIONS: list[RealtimeFunctionTool] = [SKILL_TOOL_DEFINITION]
SKILL_TOOL_NAMES = frozenset({"skill"})


@dataclass(frozen=True)
class SkillStep:
    step_id: str
    prompt: str


@dataclass(frozen=True)
class SkillDefinition:
    name: str
    description: str
    skill_type: SkillType
    body: str
    steps: tuple[SkillStep, ...]
    directory: Path
    metadata: dict[str, Any]
    source: SkillSource = "personality"


@dataclass(frozen=True)
class SkillState:
    skill_name: str
    status: SkillStatus
    step_index: int
    skill_type: SkillType

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_name": self.skill_name,
            "status": self.status,
            "step_index": self.step_index,
            "skill_type": self.skill_type,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SkillState:
        status = str(data.get("status", "")).strip()
        if status not in ("in_progress", "paused"):
            raise ValueError(f"Invalid skill status: {status!r}")
        skill_type = str(data.get("skill_type", "generic")).strip()
        if skill_type not in ("checklist", "generic", "pulse"):
            raise ValueError(f"Invalid skill type: {skill_type!r}")
        return cls(
            skill_name=str(data["skill_name"]).strip(),
            status=status,  # type: ignore[arg-type]
            step_index=int(data.get("step_index", 0)),
            skill_type=skill_type,  # type: ignore[arg-type]
        )


def _sanitize_skill_name(name: str) -> str:
    cleaned = name.strip().lower().replace(" ", "-")
    cleaned = re.sub(r"[^a-z0-9-]", "", cleaned)
    if not cleaned or not _SAFE_NAME.match(cleaned):
        raise ValueError(f"Invalid skill name: {name!r}")
    return cleaned


def _parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    stripped = content.strip()
    if not stripped.startswith("---"):
        raise ValueError("SKILL.md must begin with YAML frontmatter delimited by ---")
    parts = stripped.split("---", 2)
    if len(parts) < 3:
        raise ValueError("SKILL.md frontmatter is not closed with ---")
    raw_meta = yaml.safe_load(parts[1])
    if not isinstance(raw_meta, dict):
        raise ValueError("SKILL.md frontmatter must be a YAML mapping")
    body = parts[2].lstrip("\n")
    return raw_meta, body


def _validate_skill_name(name: str, directory_name: str) -> None:
    if name != directory_name:
        raise ValueError(
            f"Skill name {name!r} does not match directory name {directory_name!r}"
        )
    if len(name) > 64:
        raise ValueError(f"Skill name {name!r} exceeds 64 characters")
    if not _SAFE_NAME.match(name):
        raise ValueError(f"Skill name {name!r} must be lowercase letters, digits, and hyphens")


def _validate_description(description: str) -> None:
    if not description or not description.strip():
        raise ValueError("Skill description is required")
    if len(description) > 1024:
        raise ValueError("Skill description exceeds 1024 characters")


def _normalize_scope(raw_scope: str) -> Literal["persona", "shared"]:
    scope = raw_scope.strip().lower() if raw_scope else "persona"
    if scope not in ("persona", "shared"):
        raise ValueError(f"Invalid skill scope: {raw_scope!r}")
    return scope  # type: ignore[return-value]


def _normalize_skill_type(raw_type: str) -> SkillType:
    skill_type = raw_type.strip().lower() if raw_type else "generic"
    if skill_type not in ("checklist", "generic", "pulse"):
        raise ValueError(f"Invalid skill type: {raw_type!r}")
    return skill_type  # type: ignore[return-value]


def _resolve_skill_type(raw_type: str, body: str) -> SkillType:
    if raw_type.strip():
        return _normalize_skill_type(raw_type)
    if re.search(r"^##\s+Steps\s*$", body, flags=re.MULTILINE) and re.search(
        r"^###\s+",
        body,
        flags=re.MULTILINE,
    ):
        return "checklist"
    return "generic"


def _resolve_target_personality(personality_id: str) -> PersonalityProfile:
    cleaned = personality_id.strip()
    if cleaned:
        return get_personality(cleaned)
    return get_active_personality()


def _skill_dir_for_scope(
    scope: Literal["persona", "shared"],
    name: str,
    personality: PersonalityProfile,
) -> tuple[Path, SkillSource]:
    if scope == "persona":
        return personality.directory / "skills" / name, "personality"
    return _user_skills_dir() / name, "shared"


def _merge_skill_frontmatter(
    raw_meta: dict[str, Any],
    *,
    name: str,
    description: str,
    skill_type: SkillType,
    personalities: frozenset[str] | None,
) -> dict[str, Any]:
    merged = dict(raw_meta)
    merged["name"] = name
    merged["description"] = description.strip()

    metadata = merged.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}

    buddy = metadata.get("buddy")
    if not isinstance(buddy, dict):
        buddy = {}
    else:
        buddy = dict(buddy)

    if skill_type in ("checklist", "pulse"):
        buddy["type"] = skill_type
    if personalities is not None:
        buddy["personalities"] = sorted(personalities)

    if buddy:
        metadata = {**metadata, "buddy": buddy}
        merged["metadata"] = metadata
    elif "metadata" in merged:
        merged["metadata"] = metadata

    return merged


def _format_skill_md(metadata: dict[str, Any], body: str) -> str:
    yaml_body = yaml.dump(
        metadata,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    ).rstrip("\n")
    return f"---\n{yaml_body}\n---\n\n{body.strip()}\n"


def _compose_skill_md(
    name: str,
    description: str,
    body: str,
    *,
    skill_type: SkillType = "generic",
    personalities: frozenset[str] | None = None,
) -> str:
    stripped = body.strip()
    if stripped.startswith("---"):
        raw_meta, markdown_body = _parse_frontmatter(stripped)
    else:
        raw_meta = {}
        markdown_body = stripped

    merged = _merge_skill_frontmatter(
        raw_meta,
        name=name,
        description=description,
        skill_type=skill_type,
        personalities=personalities,
    )
    return _format_skill_md(merged, markdown_body)


def _write_and_validate_skill(
    content: str,
    skill_dir: Path,
    source: SkillSource,
    *,
    is_new: bool = False,
) -> SkillDefinition:
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_path = skill_dir / _SKILL_FILENAME
    previous = skill_path.read_text(encoding="utf-8") if skill_path.is_file() and not is_new else None
    skill_path.write_text(content, encoding="utf-8")
    try:
        return load_skill_definition(skill_dir, source=source)
    except Exception:
        if previous is not None:
            skill_path.write_text(previous, encoding="utf-8")
        else:
            skill_path.unlink(missing_ok=True)
            if skill_dir.is_dir() and not any(skill_dir.iterdir()):
                skill_dir.rmdir()
        raise


def create_skill(
    name: str,
    description: str,
    body: str,
    *,
    scope: str = "persona",
    personality_id: str = "",
    skill_type: str = "generic",
) -> SkillDefinition:
    sanitized = _sanitize_skill_name(name)
    _validate_description(description)
    normalized_scope = _normalize_scope(scope)
    normalized_type = _resolve_skill_type(skill_type, body)
    personality = _resolve_target_personality(personality_id)

    shared_personalities: frozenset[str] | None = None
    if normalized_scope == "shared" and personality_id.strip():
        shared_personalities = frozenset({personality.id})

    skill_dir, source = _skill_dir_for_scope(normalized_scope, sanitized, personality)
    if (skill_dir / _SKILL_FILENAME).is_file():
        raise FileExistsError(f"Skill {sanitized!r} already exists at {skill_dir}")

    content = _compose_skill_md(
        sanitized,
        description,
        body,
        skill_type=normalized_type,
        personalities=shared_personalities,
    )
    skill = _write_and_validate_skill(content, skill_dir, source, is_new=True)
    if normalized_type == "pulse":
        refs_dir = skill_dir / "references"
        refs_dir.mkdir(parents=True, exist_ok=True)
        session_path = refs_dir / "session.yaml"
        if not session_path.is_file():
            session_path.write_text(render_session_template(sanitized), encoding="utf-8")
            logger.info("Seeded pulse session.yaml for skill %r at %s", sanitized, session_path)
    logger.info(
        "Created skill %r at %s (source=%s)",
        skill.name,
        skill.directory,
        skill.source,
    )
    return skill


def _find_writable_skill_dir(
    personality: PersonalityProfile,
    skill_name: str,
    *,
    scope: str = "",
) -> tuple[Path, SkillSource]:
    sanitized = _sanitize_skill_name(skill_name)
    if scope:
        normalized = _normalize_scope(scope)
        skill_dir, source = _skill_dir_for_scope(normalized, sanitized, personality)
        if not (skill_dir / _SKILL_FILENAME).is_file():
            raise FileNotFoundError(f"Skill {skill_name!r} not found at {source} scope")
        if source == "builtin":
            raise ValueError(f"Cannot modify built-in skill {skill_name!r}")
        return skill_dir, source

    persona_dir = personality.directory / "skills" / sanitized
    if (persona_dir / _SKILL_FILENAME).is_file():
        return persona_dir, "personality"

    shared_dir = _user_skills_dir() / sanitized
    if (shared_dir / _SKILL_FILENAME).is_file():
        return shared_dir, "shared"

    raise FileNotFoundError(f"Skill {skill_name!r} not found in persona or shared scope")


def update_skill(
    name: str,
    *,
    description: str = "",
    body: str = "",
    scope: str = "",
    personality_id: str = "",
    skill_type: str = "",
) -> SkillDefinition:
    sanitized = _sanitize_skill_name(name)
    personality = _resolve_target_personality(personality_id)
    skill_dir, source = _find_writable_skill_dir(personality, sanitized, scope=scope)

    if source == "builtin":
        raise ValueError(f"Cannot modify built-in skill {sanitized!r}")

    existing = load_skill_definition(skill_dir, source=source)
    new_description = description.strip() or existing.description
    _validate_description(new_description)
    new_body = body.strip() or existing.body
    new_type = _normalize_skill_type(skill_type) if skill_type.strip() else existing.skill_type

    shared_personalities: frozenset[str] | None = None
    if source == "shared":
        try:
            shared_personalities = _parse_personality_scope(existing.metadata)
        except ValueError:
            shared_personalities = None

    content = _compose_skill_md(
        sanitized,
        new_description,
        new_body,
        skill_type=new_type,
        personalities=shared_personalities,
    )
    skill = _write_and_validate_skill(content, skill_dir, source)
    logger.info("Updated skill %r at %s", skill.name, skill.directory)
    return skill


def delete_skill(
    name: str,
    *,
    scope: str = "",
    personality_id: str = "",
) -> None:
    sanitized = _sanitize_skill_name(name)
    personality = _resolve_target_personality(personality_id)
    skill_dir, source = _find_writable_skill_dir(personality, sanitized, scope=scope)

    if source == "builtin":
        raise ValueError(f"Cannot delete built-in skill {sanitized!r}")

    shutil.rmtree(skill_dir)
    logger.info("Deleted skill %r from %s", sanitized, skill_dir)


def _slugify_step_id(heading: str) -> str:
    """Derive a safe step id from a human-readable ### heading."""
    cleaned = heading.strip()
    cleaned = re.sub(r"^(?:step\s*)?\d+[.)]\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.lower().replace(" ", "-")
    cleaned = re.sub(r"[^a-z0-9-]", "", cleaned)
    cleaned = re.sub(r"-+", "-", cleaned).strip("-")
    if not cleaned or not _SAFE_NAME.match(cleaned):
        fallback = re.sub(r"[^a-z0-9]", "", heading.lower())[:64]
        cleaned = fallback if fallback and _SAFE_NAME.match(fallback) else "step"
    return cleaned[:64]


def _unique_step_id(base: str, used: set[str]) -> str:
    if base not in used:
        return base
    suffix = 2
    while f"{base}-{suffix}" in used:
        suffix += 1
    return f"{base}-{suffix}"


def _parse_checklist_steps(body: str) -> tuple[SkillStep, ...]:
    steps_section = re.search(
        r"^##\s+Steps\s*$([\s\S]*?)(?=^##\s|\Z)",
        body,
        flags=re.MULTILINE,
    )
    if not steps_section:
        return ()

    steps: list[SkillStep] = []
    used_ids: set[str] = set()
    for match in re.finditer(
        r"^###\s+(.+?)\s*$([\s\S]*?)(?=^###\s|\Z)",
        steps_section.group(1),
        flags=re.MULTILINE,
    ):
        heading = match.group(1).strip()
        prompt = match.group(2).strip()
        if not prompt:
            continue
        step_id = _unique_step_id(_slugify_step_id(heading), used_ids)
        used_ids.add(step_id)
        steps.append(SkillStep(step_id=step_id, prompt=prompt))
    return tuple(steps)


def _skill_type_from_metadata(metadata: dict[str, Any]) -> SkillType:
    buddy_meta = metadata.get("buddy")
    if isinstance(buddy_meta, dict):
        raw_type = str(buddy_meta.get("type", "")).strip().lower()
        if raw_type == "checklist":
            return "checklist"
        if raw_type == "pulse":
            return "pulse"
    return "generic"


def _parse_personality_scope(metadata: dict[str, Any]) -> frozenset[str] | None:
    """Return None when a shared skill applies to all personalities, else allowed ids."""
    buddy_meta = metadata.get("buddy")
    if not isinstance(buddy_meta, dict):
        return None

    raw_scope = buddy_meta.get("personalities")
    if raw_scope is None:
        return None
    if isinstance(raw_scope, str) and raw_scope.strip().lower() == "all":
        return None
    if not isinstance(raw_scope, list) or not raw_scope:
        raise ValueError("metadata.buddy.personalities must be 'all' or a non-empty list of ids")

    ids: list[str] = []
    for entry in raw_scope:
        personality_id = str(entry).strip()
        if not personality_id or not _SAFE_NAME.match(personality_id):
            raise ValueError(
                f"Invalid personality id in metadata.buddy.personalities: {entry!r}"
            )
        ids.append(personality_id)
    return frozenset(ids)


def _skill_applies_to_personality(skill: SkillDefinition, personality_id: str) -> bool:
    try:
        scope = _parse_personality_scope(skill.metadata)
    except ValueError:
        return False
    if scope is None:
        return True
    return personality_id in scope


def _shared_skill_scope_payload(skill: SkillDefinition) -> str | list[str]:
    try:
        scope = _parse_personality_scope(skill.metadata)
    except ValueError:
        return "all"
    if scope is None:
        return "all"
    return sorted(scope)


def load_skill_definition(skill_dir: Path, *, source: SkillSource = "personality") -> SkillDefinition:
    skill_path = skill_dir / _SKILL_FILENAME
    if not skill_path.is_file():
        raise FileNotFoundError(f"Missing {_SKILL_FILENAME} in {skill_dir}")

    raw_meta, body = _parse_frontmatter(skill_path.read_text(encoding="utf-8"))
    name = str(raw_meta.get("name", "")).strip()
    description = str(raw_meta.get("description", "")).strip()
    directory_name = skill_dir.name

    _validate_skill_name(name, directory_name)
    _validate_description(description)

    metadata = raw_meta.get("metadata")
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be a mapping")

    skill_type = _skill_type_from_metadata(metadata)
    steps = _parse_checklist_steps(body) if skill_type == "checklist" else ()
    if skill_type == "checklist" and not steps:
        raise ValueError(
            f"Checklist skill {name!r} has no valid steps under ## Steps. "
            "Each step needs a ### heading and non-empty prompt text on the following lines."
        )

    return SkillDefinition(
        name=name,
        description=description,
        skill_type=skill_type,
        body=body,
        steps=steps,
        directory=skill_dir.resolve(),
        metadata=metadata,
        source=source,
    )


def _built_in_skills_dir() -> Path:
    from buddy_tools.infra.data_dir import get_built_in_skills_dir

    return get_built_in_skills_dir()


def _user_skills_dir() -> Path:
    from buddy_tools.infra.data_dir import get_user_skills_dir

    return get_user_skills_dir()


def _discover_skills_in_directory(
    skills_root: Path,
    *,
    source: SkillSource,
) -> dict[str, SkillDefinition]:
    if not skills_root.is_dir():
        return {}

    definitions: dict[str, SkillDefinition] = {}
    for skill_dir in sorted(skills_root.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_path = skill_dir / _SKILL_FILENAME
        if not skill_path.is_file():
            continue
        try:
            skill = load_skill_definition(skill_dir, source=source)
            definitions[skill.name] = skill
        except (ValueError, OSError) as exc:
            logger.warning("Skipping invalid skill in %s: %s", skill_dir, exc)
    return definitions


def _discover_shared_skills(personality_id: str) -> dict[str, SkillDefinition]:
    skills_root = _user_skills_dir()
    if not skills_root.is_dir():
        return {}

    definitions: dict[str, SkillDefinition] = {}
    for skill_dir in sorted(skills_root.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_path = skill_dir / _SKILL_FILENAME
        if not skill_path.is_file():
            continue
        try:
            skill = load_skill_definition(skill_dir, source="shared")
            if not _skill_applies_to_personality(skill, personality_id):
                continue
            definitions[skill.name] = skill
        except (ValueError, OSError) as exc:
            logger.warning("Skipping invalid shared skill in %s: %s", skill_dir, exc)
    return definitions


def discover_skills(personality: PersonalityProfile) -> list[SkillDefinition]:
    """Merge built-in, shared user, and persona skills for the active personality.

    Precedence on name collision: personality > shared > built-in.
    """
    merged: dict[str, SkillDefinition] = {}
    merged.update(_discover_skills_in_directory(_built_in_skills_dir(), source="builtin"))
    merged.update(_discover_shared_skills(personality.id))
    merged.update(
        _discover_skills_in_directory(
            personality.directory / "skills",
            source="personality",
        )
    )
    return [merged[name] for name in sorted(merged)]


def get_skill_definition(personality: PersonalityProfile, skill_name: str) -> SkillDefinition:
    sanitized = _sanitize_skill_name(skill_name)

    persona_dir = personality.directory / "skills" / sanitized
    if persona_dir.is_dir() and (persona_dir / _SKILL_FILENAME).is_file():
        return load_skill_definition(persona_dir, source="personality")

    shared_dir = _user_skills_dir() / sanitized
    if shared_dir.is_dir() and (shared_dir / _SKILL_FILENAME).is_file():
        skill = load_skill_definition(shared_dir, source="shared")
        if _skill_applies_to_personality(skill, personality.id):
            return skill

    builtin_dir = _built_in_skills_dir() / sanitized
    if builtin_dir.is_dir() and (builtin_dir / _SKILL_FILENAME).is_file():
        return load_skill_definition(builtin_dir, source="builtin")

    raise FileNotFoundError(f"Skill {skill_name!r} not found")


def _skill_state_path(memory_root: Path, persona_namespace: str) -> Path:
    persona_dir = persona_memory_dir(memory_root, persona_namespace)
    path = (persona_dir / _SKILL_STATE_FILENAME).resolve()
    if path.parent != persona_dir.resolve():
        raise ValueError("Invalid skill state path")
    return path


def load_skill_state(memory_root: Path, persona_namespace: str) -> SkillState | None:
    path = _skill_state_path(memory_root, persona_namespace)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("skill_state.json must be an object")
        return SkillState.from_dict(data)
    except (json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
        logger.warning("Could not load skill state from %s: %s", path, exc)
        return None


def save_skill_state(
    memory_root: Path,
    persona_namespace: str,
    state: SkillState,
) -> None:
    path = _skill_state_path(memory_root, persona_namespace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state.to_dict(), indent=2) + "\n", encoding="utf-8")
    logger.info(
        "Saved skill state: skill=%r status=%r step=%d",
        state.skill_name,
        state.status,
        state.step_index,
    )


def clear_skill_state(memory_root: Path, persona_namespace: str) -> None:
    path = _skill_state_path(memory_root, persona_namespace)
    if path.is_file():
        path.unlink()
        logger.info("Cleared skill state for namespace %r", persona_namespace)


def _resolve_resource_path(skill: SkillDefinition, relative_path: str) -> Path:
    normalized = relative_path.strip().replace("\\", "/").lstrip("/")
    if not normalized or ".." in normalized.split("/"):
        raise ValueError(f"Invalid skill file path: {relative_path!r}")

    parts = normalized.split("/", 1)
    if len(parts) != 2:
        raise ValueError(
            f"Path must be under references/, scripts/, or assets/: {relative_path!r}"
        )
    resource_dir, filename = parts
    if resource_dir not in _RESOURCE_DIRS:
        raise ValueError(
            f"Path must be under references/, scripts/, or assets/: {relative_path!r}"
        )
    if not filename or filename.endswith("/"):
        raise ValueError(f"Invalid skill file path: {relative_path!r}")

    base = (skill.directory / resource_dir).resolve()
    path = (base / filename).resolve()
    if path.parent != base or skill.directory.resolve() not in path.parents:
        raise ValueError(f"Invalid skill file path: {relative_path!r}")
    return path


def _step_prompt(skill: SkillDefinition, step_index: int) -> str | None:
    if skill.skill_type != "checklist" or not skill.steps:
        return None
    if step_index < 0 or step_index >= len(skill.steps):
        return None
    return skill.steps[step_index].prompt


def _format_step_message(
    skill: SkillDefinition,
    step_index: int,
    *,
    prefix: str,
) -> str:
    if skill.skill_type == "checklist" and skill.steps:
        step = skill.steps[step_index]
        total = len(skill.steps)
        return (
            f"{prefix} Skill {skill.name!r}: step {step_index + 1} of {total} "
            f"({step.step_id}). {step.prompt}"
        )
    return f"{prefix} Skill {skill.name!r} is active."


def build_skill_instructions() -> str:
    return (
        "You have skills — structured guided workflows from built-ins, shared user skills, "
        "and the active persona. Use the skill tool with an action:\n"
        "- skill(action=list): discover available skills (metadata, source: builtin/shared/personality, "
        "and scope for shared skills)\n"
        "- skill(action=create): write a new skill to the active persona's skills/ folder by default; "
        "use scope shared only when the user wants cross-persona placement\n"
        "- skill(action=update) / skill(action=delete): change or remove persona or shared skills "
        "(not built-ins)\n"
        "- skill(action=start): begin or resume a skill by name\n"
        "- skill(action=status): check current step and progress\n"
        "- skill(action=advance): move to the next checklist step after the user confirms verbally\n"
        "- skill(action=read_file): load reference material from the active skill or a named skill "
        "(references/, scripts/, assets/)\n"
        "- skill(action=write_file): write resource files for a persona or shared skill (not built-ins)\n"
        "- skill(action=update_pulse_config): tune pulse timing and cameras in references/session.yaml "
        "(re-start skill to apply)\n"
        "- skill(action=pause) / skill(action=cancel): suspend or abandon the active skill\n"
        "For checklist skills, walk one step at a time. Wait for verbal confirmation before "
        "calling skill(action=advance). The tool returns the authoritative next step — do not invent "
        "step order. When authoring skills, prefer persona scope unless the user asks for a shared skill."
    )


SKILL_TOOL_GROUP = ToolGroup(
    id="skills",
    title="Skills",
    when_to_use=(
        "User wants a guided workflow, checklist, pulse session, or to list/start/"
        "create/update skills for the active persona."
    ),
    tools=(SKILL_TOOL_DEFINITION,),
    instructions=build_skill_instructions(),
)


def build_pulse_context(
    memory_root: Path,
    persona_namespace: str,
    personality: PersonalityProfile,
    *,
    include_full_skill_body: bool = False,
) -> str:
    pulse = load_pulse_state(memory_root, persona_namespace)
    if pulse is None or pulse.status not in ("active", "paused"):
        return ""

    try:
        skill = get_skill_definition(personality, pulse.skill_name)
    except (FileNotFoundError, ValueError) as exc:
        logger.warning("Active pulse skill %r not loadable: %s", pulse.skill_name, exc)
        return ""

    lines = ["Active pulse session:"]
    status_label = "paused" if pulse.status == "paused" else "running"
    lines.append(
        f"- Skill {skill.name!r} is {status_label} (phase: {pulse.phase}, ticks: {pulse.tick_count})."
    )
    if pulse.last_tick_at:
        lines.append(f"- Last worker tick: {pulse.last_tick_at}.")
    if pulse.pending_cue:
        if pulse.fold_on_next_reply:
            lines.append(
                f"- Pending cue ({pulse.cue_priority or 'mandatory'}): {pulse.pending_cue} "
                "(deferred while user was speaking — weave into the next reply)."
            )
        else:
            lines.append(
                f"- Pending cue ({pulse.cue_priority or 'mandatory'}): {pulse.pending_cue}"
            )
    session = pulse.get_session_config()
    if session is not None and session.pulse.silence_gated_only:
        lines.append(
            "- Silence-gated-only mode is active: suppress reactive speech; "
            "speak only on extended silence or mandatory cues."
        )
    if pulse.vars:
        lines.append(f"- Runtime vars: {json.dumps(pulse.vars, sort_keys=True)}")

    if include_full_skill_body:
        lines.append("")
        lines.append(f"## Pulse skill instructions: {skill.name}")
        lines.append(skill.body)

    return "\n".join(lines)


def build_active_skill_context(
    memory_root: Path,
    persona_namespace: str,
    personality: PersonalityProfile,
    *,
    include_full_skill_body: bool = False,
) -> str:
    state = load_skill_state(memory_root, persona_namespace)
    if state is None or state.status not in ("in_progress", "paused"):
        return ""

    if state.skill_type == "pulse":
        return build_pulse_context(
            memory_root,
            persona_namespace,
            personality,
            include_full_skill_body=include_full_skill_body,
        )

    try:
        skill = get_skill_definition(personality, state.skill_name)
    except (FileNotFoundError, ValueError) as exc:
        logger.warning("Active skill %r not loadable: %s", state.skill_name, exc)
        return ""

    lines = ["Active skill context:"]
    if state.status == "paused":
        lines.append(f"- Skill {skill.name!r} is paused at step {state.step_index + 1}.")
    else:
        if skill.skill_type == "checklist" and skill.steps:
            total = len(skill.steps)
            step_prompt = _step_prompt(skill, state.step_index) or ""
            lines.append(
                f"- Skill {skill.name!r}: step {state.step_index + 1} of {total}. "
                f"Current prompt: {step_prompt}"
            )
        else:
            lines.append(f"- Skill {skill.name!r} is in progress.")

    if include_full_skill_body:
        lines.append("")
        lines.append(f"## Skill instructions: {skill.name}")
        lines.append(skill.body)

    return "\n".join(lines)


def execute_skill_tool(
    memory_root: Path,
    persona_namespace: str,
    tool_name: str,
    args: dict[str, Any],
) -> ToolExecutionResult:
    if tool_name == "skill":
        resolved = resolve_action_args("skill", args, SKILL_ACTIONS)
        if isinstance(resolved, ToolExecutionResult):
            return resolved
        tool_name, args = resolved

    personality = get_active_personality()
    memory_root.mkdir(parents=True, exist_ok=True)

    if tool_name == "list_skills":
        skills = discover_skills(personality)
        payload: list[dict[str, Any]] = []
        for skill in skills:
            entry: dict[str, Any] = {
                "name": skill.name,
                "description": skill.description,
                "source": skill.source,
            }
            if skill.source == "shared":
                entry["scope"] = _shared_skill_scope_payload(skill)
            payload.append(entry)
        return ToolExecutionResult(output=json.dumps(payload))

    if tool_name == "start_skill":
        return _start_skill(memory_root, persona_namespace, personality, args)

    if tool_name == "read_skill_file":
        return _read_skill_file(memory_root, persona_namespace, personality, args)

    if tool_name == "write_skill_file":
        return _write_skill_file(personality, args)

    if tool_name == "update_pulse_config":
        return _update_pulse_config(personality, args)

    if tool_name == "skill_status":
        return _skill_status(memory_root, persona_namespace, personality)

    if tool_name == "advance_skill":
        return _advance_skill(memory_root, persona_namespace, personality, args)

    if tool_name == "pause_skill":
        return _pause_skill(memory_root, persona_namespace)

    if tool_name == "cancel_skill":
        return _cancel_skill(memory_root, persona_namespace)

    if tool_name == "create_skill":
        return _create_skill_tool(args)

    if tool_name == "update_skill":
        return _update_skill_tool(args)

    if tool_name == "delete_skill":
        return _delete_skill_tool(args)

    return tool_error(tool_name, f"unknown skill tool {tool_name!r}")


def _teardown_pulse_session(memory_root: Path, persona_namespace: str, skill_name: str) -> None:
    stop_pulse_worker(persona_namespace)
    clear_pulse_state(memory_root, persona_namespace)
    cancel_timers_for_skill(skill_name)


def teardown_persisted_skill_session(
    memory_root: Path,
    persona_namespace: str,
    *,
    reason: str,
) -> str | None:
    """Tear down any active skill persisted on disk (startup/shutdown cleanup).

    Mirrors ``cancel_skill`` without requiring a live tool call. Returns the
    skill name when something was torn down, else ``None``.
    """
    state = load_skill_state(memory_root, persona_namespace)
    pulse = load_pulse_state(memory_root, persona_namespace)

    active_state = state is not None and state.status in ("in_progress", "paused")
    active_pulse = pulse is not None and pulse.status in ("active", "paused")
    if not active_state and not active_pulse:
        return None

    if active_state:
        skill_name = state.skill_name
        skill_type = state.skill_type
    else:
        assert pulse is not None
        skill_name = pulse.skill_name
        skill_type = "pulse"

    logger.info(
        "Tearing down persisted skill on %s: skill=%r type=%r persona_namespace=%r",
        reason,
        skill_name,
        skill_type,
        persona_namespace,
    )

    if skill_type == "pulse":
        _teardown_pulse_session(memory_root, persona_namespace, skill_name)
    else:
        cancel_timers_for_skill(skill_name)
        if active_pulse:
            _teardown_pulse_session(memory_root, persona_namespace, pulse.skill_name)

    clear_skill_state(memory_root, persona_namespace)
    return skill_name


def _start_skill(
    memory_root: Path,
    persona_namespace: str,
    personality: PersonalityProfile,
    args: dict[str, Any],
) -> ToolExecutionResult:
    raw_name = str(args.get("name", "")).strip()
    if not raw_name:
        return tool_error("start_skill", "skill name is empty", context=safe_tool_context(args))

    try:
        skill = get_skill_definition(personality, raw_name)
    except ValueError as exc:
        return tool_error("start_skill", str(exc), context=safe_tool_context(args))
    except FileNotFoundError:
        return tool_error("start_skill", f"skill {raw_name!r} not found", context=safe_tool_context(args))

    existing = load_skill_state(memory_root, persona_namespace)
    if existing and existing.skill_name != skill.name:
        if existing.skill_type == "pulse":
            _teardown_pulse_session(memory_root, persona_namespace, existing.skill_name)
        cancel_timers_for_skill(existing.skill_name)

    if skill.skill_type == "pulse":
        return _start_pulse_skill(memory_root, persona_namespace, skill, existing)

    if existing and existing.skill_name == skill.name and existing.status in ("in_progress", "paused"):
        existing = SkillState(
            skill_name=existing.skill_name,
            status="in_progress",
            step_index=existing.step_index,
            skill_type=existing.skill_type,
        )
        save_skill_state(memory_root, persona_namespace, existing)
        message = _format_step_message(
            skill,
            existing.step_index,
            prefix="Resumed",
        )
        return ToolExecutionResult(
            output=message,
            refresh_instructions=True,
            include_full_skill_body=True,
        )

    state = SkillState(
        skill_name=skill.name,
        status="in_progress",
        step_index=0,
        skill_type=skill.skill_type,
    )
    save_skill_state(memory_root, persona_namespace, state)
    message = _format_step_message(skill, 0, prefix="Started")
    return ToolExecutionResult(
        output=message,
        refresh_instructions=True,
        include_full_skill_body=True,
    )


def _start_pulse_skill(
    memory_root: Path,
    persona_namespace: str,
    skill: SkillDefinition,
    existing: SkillState | None,
) -> ToolExecutionResult:
    existing_pulse = load_pulse_state(memory_root, persona_namespace)
    if (
        existing
        and existing.skill_name == skill.name
        and existing.status in ("in_progress", "paused")
        and existing_pulse is not None
    ):
        resumed_pulse = PulseState(
            skill_name=existing_pulse.skill_name,
            status="active",
            tick_count=existing_pulse.tick_count,
            started_at=existing_pulse.started_at,
            last_tick_at=existing_pulse.last_tick_at,
            phase=existing_pulse.phase,
            tick_interval_seconds=existing_pulse.tick_interval_seconds,
            pending_cue=existing_pulse.pending_cue,
            cue_priority=existing_pulse.cue_priority,
            pulse_mode=existing_pulse.pulse_mode,
            narrator_muted=existing_pulse.narrator_muted,
            fired_rules=list(existing_pulse.fired_rules),
            vars=dict(existing_pulse.vars),
            session_config=dict(existing_pulse.session_config),
            last_user_speech_at=existing_pulse.last_user_speech_at,
            last_assistant_speech_at=existing_pulse.last_assistant_speech_at,
            pending_cue_since=existing_pulse.pending_cue_since,
            pulse_in_flight=False,
        )
        save_pulse_state(memory_root, persona_namespace, resumed_pulse)
        save_skill_state(
            memory_root,
            persona_namespace,
            SkillState(
                skill_name=skill.name,
                status="in_progress",
                step_index=0,
                skill_type="pulse",
            ),
        )
        start_pulse_worker(
            memory_root,
            persona_namespace,
            skill.name,
            tick_interval_seconds=resumed_pulse.tick_interval_seconds,
        )
        return ToolExecutionResult(
            output=f"Resumed pulse session {skill.name!r} (phase: {resumed_pulse.phase}).",
            refresh_instructions=True,
            include_full_skill_body=True,
        )

    try:
        pulse_state = init_pulse_state_from_skill(skill.name, skill.directory)
    except SessionValidationError as exc:
        logger.warning("Could not start pulse skill %r: %s", skill.name, exc)
        return tool_error("start_skill", str(exc))

    save_pulse_state(memory_root, persona_namespace, pulse_state)
    save_skill_state(
        memory_root,
        persona_namespace,
        SkillState(
            skill_name=skill.name,
            status="in_progress",
            step_index=0,
            skill_type="pulse",
        ),
    )
    start_pulse_worker(
        memory_root,
        persona_namespace,
        skill.name,
        tick_interval_seconds=pulse_state.tick_interval_seconds,
    )
    return ToolExecutionResult(
        output=(
            f"Started pulse session {skill.name!r} (phase: {pulse_state.phase}). "
            "The runtime worker is active; narrate when directed."
        ),
        refresh_instructions=True,
        include_full_skill_body=True,
    )


def _read_skill_file(
    memory_root: Path,
    persona_namespace: str,
    personality: PersonalityProfile,
    args: dict[str, Any],
) -> ToolExecutionResult:
    raw_skill_name = str(args.get("skill_name", "")).strip()
    try:
        if raw_skill_name:
            skill = get_skill_definition(personality, raw_skill_name)
        else:
            state = load_skill_state(memory_root, persona_namespace)
            if state is None:
                return tool_error(
                    "read_skill_file",
                    "no active skill (provide skill_name to read from a named skill)",
                    context=safe_tool_context(args),
                )
            skill = get_skill_definition(personality, state.skill_name)
        path = _resolve_resource_path(skill, str(args.get("path", "")))
    except (ValueError, FileNotFoundError) as exc:
        return tool_error("read_skill_file", str(exc), context=safe_tool_context(args))

    if not path.is_file():
        return tool_error(
            "read_skill_file",
            f"file not found: {path.name}",
            context=safe_tool_context(args),
        )
    return ToolExecutionResult(output=path.read_text(encoding="utf-8"))


def _write_skill_file(
    personality: PersonalityProfile,
    args: dict[str, Any],
) -> ToolExecutionResult:
    skill_name = str(args.get("skill_name", "")).strip()
    relative_path = str(args.get("path", "")).strip()
    content = args.get("content")
    if not skill_name:
        return tool_error("write_skill_file", "skill_name is required", context=safe_tool_context(args))
    if content is None:
        return tool_error("write_skill_file", "content is required", context=safe_tool_context(args))
    if not isinstance(content, str):
        return tool_error("write_skill_file", "content must be a string", context=safe_tool_context(args))

    try:
        skill_dir, source = _find_writable_skill_dir(personality, skill_name)
        skill = load_skill_definition(skill_dir, source=source)
        path = _resolve_resource_path(skill, relative_path)
    except (ValueError, FileNotFoundError) as exc:
        return tool_error("write_skill_file", str(exc), context=safe_tool_context(args))

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        if path.name == "session.yaml" and path.parent.name == "references":
            raw = yaml.safe_load(content)
            if raw is None:
                raw = {}
            if not isinstance(raw, dict):
                raise ValueError("session.yaml root must be a mapping")
            parse_session_config(raw, skill_name=skill.name)
    except (yaml.YAMLError, SessionValidationError, ValueError) as exc:
        return tool_error(
            "write_skill_file",
            f"invalid session.yaml: {exc}",
            context=safe_tool_context(args),
        )
    except OSError as exc:
        return tool_error("write_skill_file", f"could not write file: {exc}", context=safe_tool_context(args))

    logger.info("Wrote skill file %s for skill %r", path, skill.name)
    return ToolExecutionResult(
        output=f"Wrote {relative_path} for skill {skill.name!r}.",
    )


def _update_pulse_config(
    personality: PersonalityProfile,
    args: dict[str, Any],
) -> ToolExecutionResult:
    skill_name = str(args.get("skill_name", "")).strip()
    params = args.get("params")
    if not skill_name:
        return tool_error("update_pulse_config", "skill_name is required", context=safe_tool_context(args))
    if not isinstance(params, dict):
        return tool_error("update_pulse_config", "params must be a JSON object", context=safe_tool_context(args))

    try:
        skill_dir, source = _find_writable_skill_dir(personality, skill_name)
        skill = load_skill_definition(skill_dir, source=source)
    except (ValueError, FileNotFoundError) as exc:
        return tool_error("update_pulse_config", str(exc), context=safe_tool_context(args))

    if skill.skill_type != "pulse":
        return tool_error(
            "update_pulse_config",
            f"skill {skill.name!r} is not a pulse skill",
            context=safe_tool_context(args),
        )

    try:
        apply_pulse_config(skill_dir, params, skill_name=skill.name)
    except SessionValidationError as exc:
        return tool_error("update_pulse_config", str(exc), context=safe_tool_context(args))
    except OSError as exc:
        return tool_error("update_pulse_config", f"could not write session.yaml: {exc}", context=safe_tool_context(args))

    changed = ", ".join(sorted(params))
    logger.info("Updated pulse config for skill %r: %s", skill.name, changed)
    return ToolExecutionResult(
        output=(
            f"Updated pulse config for {skill.name!r} ({changed}). "
            "Cancel and re-start the skill to apply changes to a running session."
        ),
    )


def _skill_status(
    memory_root: Path,
    persona_namespace: str,
    personality: PersonalityProfile,
) -> ToolExecutionResult:
    state = load_skill_state(memory_root, persona_namespace)
    if state is None:
        return ToolExecutionResult(output=json.dumps({"active": False}))

    try:
        skill = get_skill_definition(personality, state.skill_name)
    except (FileNotFoundError, ValueError) as exc:
        return tool_error("skill_status", str(exc))

    payload: dict[str, Any] = {
        "active": True,
        "skill_name": state.skill_name,
        "status": state.status,
        "skill_type": state.skill_type,
        "step_index": state.step_index,
    }
    if state.skill_type == "pulse":
        pulse = load_pulse_state(memory_root, persona_namespace)
        if pulse is not None:
            payload["phase"] = pulse.phase
            payload["tick_count"] = pulse.tick_count
            payload["tick_interval_seconds"] = pulse.tick_interval_seconds
        return ToolExecutionResult(output=json.dumps(payload))
    if skill.skill_type == "checklist":
        payload["total_steps"] = len(skill.steps)
        step_prompt = _step_prompt(skill, state.step_index)
        if step_prompt:
            payload["current_step_prompt"] = step_prompt
    return ToolExecutionResult(output=json.dumps(payload))


def _advance_skill(
    memory_root: Path,
    persona_namespace: str,
    personality: PersonalityProfile,
    args: dict[str, Any],
) -> ToolExecutionResult:
    state = load_skill_state(memory_root, persona_namespace)
    if state is None or state.status not in ("in_progress", "paused"):
        return tool_error("advance_skill", "no active skill to advance", context=safe_tool_context(args))

    try:
        skill = get_skill_definition(personality, state.skill_name)
    except (FileNotFoundError, ValueError) as exc:
        return tool_error("advance_skill", str(exc), context=safe_tool_context(args))

    if skill.skill_type != "checklist" or not skill.steps:
        return tool_error("advance_skill", "active skill is not a checklist", context=safe_tool_context(args))

    skip = bool(args.get("skip", False))
    reason = str(args.get("reason", "")).strip()
    next_index = state.step_index + 1

    if next_index >= len(skill.steps):
        clear_skill_state(memory_root, persona_namespace)
        return ToolExecutionResult(
            output=f"Completed skill {skill.name!r}. All steps done.",
            refresh_instructions=True,
        )

    new_state = SkillState(
        skill_name=state.skill_name,
        status="in_progress",
        step_index=next_index,
        skill_type=state.skill_type,
    )
    save_skill_state(memory_root, persona_namespace, new_state)

    prefix = "Skipped step" if skip else "Advanced"
    if skip and reason:
        prefix = f"Skipped step ({reason})"
    message = _format_step_message(skill, next_index, prefix=prefix)
    return ToolExecutionResult(
        output=message,
        refresh_instructions=True,
    )


def _pause_skill(memory_root: Path, persona_namespace: str) -> ToolExecutionResult:
    state = load_skill_state(memory_root, persona_namespace)
    if state is None or state.status not in ("in_progress", "paused"):
        return tool_error("pause_skill", "no active skill to pause")

    if state.status == "paused":
        return ToolExecutionResult(output=f"Skill {state.skill_name!r} is already paused.")

    if state.skill_type == "pulse":
        pulse = load_pulse_state(memory_root, persona_namespace)
        if pulse is not None:
            paused_pulse = PulseState(
                skill_name=pulse.skill_name,
                status="paused",
                tick_count=pulse.tick_count,
                started_at=pulse.started_at,
                last_tick_at=pulse.last_tick_at,
                phase=pulse.phase,
                tick_interval_seconds=pulse.tick_interval_seconds,
                pending_cue=pulse.pending_cue,
                cue_priority=pulse.cue_priority,
                pulse_mode=pulse.pulse_mode,
                narrator_muted=pulse.narrator_muted,
                fired_rules=list(pulse.fired_rules),
                vars=dict(pulse.vars),
                session_config=dict(pulse.session_config),
                last_user_speech_at=pulse.last_user_speech_at,
                last_assistant_speech_at=pulse.last_assistant_speech_at,
                pending_cue_since=pulse.pending_cue_since,
                pulse_in_flight=False,
            )
            save_pulse_state(memory_root, persona_namespace, paused_pulse)
        stop_pulse_worker(persona_namespace)
        save_skill_state(
            memory_root,
            persona_namespace,
            SkillState(
                skill_name=state.skill_name,
                status="paused",
                step_index=0,
                skill_type="pulse",
            ),
        )
        return ToolExecutionResult(
            output=f"Paused pulse session {state.skill_name!r}.",
            refresh_instructions=True,
        )

    paused = SkillState(
        skill_name=state.skill_name,
        status="paused",
        step_index=state.step_index,
        skill_type=state.skill_type,
    )
    save_skill_state(memory_root, persona_namespace, paused)
    return ToolExecutionResult(
        output=f"Paused skill {state.skill_name!r} at step {state.step_index + 1}.",
        refresh_instructions=True,
    )


def _cancel_skill(memory_root: Path, persona_namespace: str) -> ToolExecutionResult:
    skill_name = teardown_persisted_skill_session(
        memory_root,
        persona_namespace,
        reason="cancel_skill",
    )
    if skill_name is None:
        return tool_error("cancel_skill", "no active skill to cancel")

    return ToolExecutionResult(
        output=f"Cancelled skill {skill_name!r}.",
        refresh_instructions=True,
    )


def _create_skill_tool(args: dict[str, Any]) -> ToolExecutionResult:
    name = str(args.get("name", "")).strip()
    description = str(args.get("description", "")).strip()
    body = str(args.get("body", "")).strip()
    if not name or not description or not body:
        return tool_error(
            "create_skill",
            "name, description, and body are required",
            context=safe_tool_context(args),
        )
    try:
        skill = create_skill(
            name,
            description,
            body,
            scope=str(args.get("scope", "persona")),
            personality_id=str(args.get("personality_id", "")),
            skill_type=str(args.get("skill_type", "")),
        )
    except (ValueError, FileExistsError, OSError) as exc:
        return tool_error("create_skill", str(exc), context=safe_tool_context(args))
    return ToolExecutionResult(
        output=(
            f"Created skill {skill.name!r} at {skill.directory} "
            f"(source: {skill.source})."
        )
    )


def _update_skill_tool(args: dict[str, Any]) -> ToolExecutionResult:
    name = str(args.get("name", "")).strip()
    if not name:
        return tool_error("update_skill", "name is required", context=safe_tool_context(args))
    try:
        skill = update_skill(
            name,
            description=str(args.get("description", "")),
            body=str(args.get("body", "")),
            scope=str(args.get("scope", "")),
            personality_id=str(args.get("personality_id", "")),
            skill_type=str(args.get("skill_type", "")),
        )
    except (ValueError, FileNotFoundError, OSError) as exc:
        return tool_error("update_skill", str(exc), context=safe_tool_context(args))
    return ToolExecutionResult(output=f"Updated skill {skill.name!r} at {skill.directory}.")


def _delete_skill_tool(args: dict[str, Any]) -> ToolExecutionResult:
    name = str(args.get("name", "")).strip()
    if not name:
        return tool_error("delete_skill", "name is required", context=safe_tool_context(args))
    try:
        delete_skill(
            name,
            scope=str(args.get("scope", "")),
            personality_id=str(args.get("personality_id", "")),
        )
    except (ValueError, FileNotFoundError, OSError) as exc:
        return tool_error("delete_skill", str(exc), context=safe_tool_context(args))
    return ToolExecutionResult(output=f"Deleted skill {name!r}.")
