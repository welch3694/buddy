"""Personality skills: Agent Skills layout, runtime state, and LLM tools."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from openai.types.realtime import RealtimeFunctionTool

from buddy_tools.memory import persona_memory_dir
from buddy_tools.personality import PersonalityProfile, get_active_personality
from buddy_tools.result import ToolExecutionResult

logger = logging.getLogger(__name__)

_SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_SKILL_FILENAME = "SKILL.md"
_SKILL_STATE_FILENAME = "skill_state.json"
_RESOURCE_DIRS = frozenset({"references", "scripts", "assets"})
SkillStatus = Literal["in_progress", "paused"]
SkillType = Literal["checklist", "generic"]
SkillSource = Literal["builtin", "personality"]

SKILL_TOOL_DEFINITIONS: list[RealtimeFunctionTool] = [
    RealtimeFunctionTool(
        type="function",
        name="list_skills",
        description=(
            "List available skills: global built-in workflows plus any scoped to the active "
            "personality (name, description, and source). Use when the user asks what guided "
            "workflows or checklists are available."
        ),
        parameters={"type": "object", "properties": {}},
    ),
    RealtimeFunctionTool(
        type="function",
        name="start_skill",
        description=(
            "Begin or resume a skill by name. Use when the user wants to run a checklist, "
            "guided setup, or other structured workflow."
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Skill name, e.g. equipment-setup",
                }
            },
            "required": ["name"],
        },
    ),
    RealtimeFunctionTool(
        type="function",
        name="read_skill_file",
        description=(
            "Read a file from the active skill's references/, scripts/, or assets/ folder. "
            "Use only when the skill instructions call for detailed reference material."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path under references/, scripts/, or assets/, e.g. references/checklist.md",
                }
            },
            "required": ["path"],
        },
    ),
    RealtimeFunctionTool(
        type="function",
        name="skill_status",
        description="Get the current active skill, step progress, and current step prompt.",
        parameters={"type": "object", "properties": {}},
    ),
    RealtimeFunctionTool(
        type="function",
        name="advance_skill",
        description=(
            "Mark the current checklist step complete and advance to the next step. "
            "Call when the user verbally confirms they finished the current step."
        ),
        parameters={
            "type": "object",
            "properties": {
                "skip": {
                    "type": "boolean",
                    "description": "Skip the current step instead of completing it",
                },
                "reason": {
                    "type": "string",
                    "description": "Optional reason when skipping a step",
                },
            },
        },
    ),
    RealtimeFunctionTool(
        type="function",
        name="pause_skill",
        description="Suspend the active skill while keeping progress so it can be resumed later.",
        parameters={"type": "object", "properties": {}},
    ),
    RealtimeFunctionTool(
        type="function",
        name="cancel_skill",
        description="Cancel the active skill and clear all progress.",
        parameters={"type": "object", "properties": {}},
    ),
]

SKILL_TOOL_NAMES = frozenset(tool.name for tool in SKILL_TOOL_DEFINITIONS)


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
        if skill_type not in ("checklist", "generic"):
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


def _parse_checklist_steps(body: str) -> tuple[SkillStep, ...]:
    steps_section = re.search(
        r"^##\s+Steps\s*$([\s\S]*?)(?=^##\s|\Z)",
        body,
        flags=re.MULTILINE,
    )
    if not steps_section:
        return ()

    steps: list[SkillStep] = []
    for match in re.finditer(
        r"^###\s+([a-z0-9][a-z0-9_-]*)\s*\n([\s\S]*?)(?=^###\s|\Z)",
        steps_section.group(1),
        flags=re.MULTILINE,
    ):
        step_id = match.group(1).strip()
        prompt = match.group(2).strip()
        if prompt:
            steps.append(SkillStep(step_id=step_id, prompt=prompt))
    return tuple(steps)


def _skill_type_from_metadata(metadata: dict[str, Any]) -> SkillType:
    buddy_meta = metadata.get("buddy")
    if isinstance(buddy_meta, dict):
        raw_type = str(buddy_meta.get("type", "")).strip().lower()
        if raw_type == "checklist":
            return "checklist"
    return "generic"


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
        raise ValueError(f"Checklist skill {name!r} has no steps under ## Steps")

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
    from buddy_tools.data_dir import get_built_in_skills_dir

    return get_built_in_skills_dir()


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


def discover_skills(personality: PersonalityProfile) -> list[SkillDefinition]:
    """Merge global built-in skills with the active personality's skills.

    Persona-scoped skills override built-ins when names collide.
    """
    merged: dict[str, SkillDefinition] = {}
    merged.update(_discover_skills_in_directory(_built_in_skills_dir(), source="builtin"))
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
        "You have skills — structured guided workflows from global built-ins and the active persona:\n"
        "- list_skills: discover available skills (metadata and source: builtin or personality)\n"
        "- start_skill: begin or resume a skill by name\n"
        "- skill_status: check current step and progress\n"
        "- advance_skill: move to the next checklist step after the user confirms verbally\n"
        "- read_skill_file: load reference material from the active skill when instructions require it\n"
        "- pause_skill / cancel_skill: suspend or abandon the active skill\n"
        "For checklist skills, walk one step at a time. Wait for verbal confirmation before "
        "calling advance_skill. The tool returns the authoritative next step — do not invent step order."
    )


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
    personality = get_active_personality()
    memory_root.mkdir(parents=True, exist_ok=True)

    if tool_name == "list_skills":
        skills = discover_skills(personality)
        payload = [
            {"name": s.name, "description": s.description, "source": s.source}
            for s in skills
        ]
        return ToolExecutionResult(output=json.dumps(payload))

    if tool_name == "start_skill":
        return _start_skill(memory_root, persona_namespace, personality, args)

    if tool_name == "read_skill_file":
        return _read_skill_file(memory_root, persona_namespace, personality, args)

    if tool_name == "skill_status":
        return _skill_status(memory_root, persona_namespace, personality)

    if tool_name == "advance_skill":
        return _advance_skill(memory_root, persona_namespace, personality, args)

    if tool_name == "pause_skill":
        return _pause_skill(memory_root, persona_namespace)

    if tool_name == "cancel_skill":
        return _cancel_skill(memory_root, persona_namespace)

    return ToolExecutionResult(output=f"Error: unknown skill tool {tool_name!r}")


def _start_skill(
    memory_root: Path,
    persona_namespace: str,
    personality: PersonalityProfile,
    args: dict[str, Any],
) -> ToolExecutionResult:
    raw_name = str(args.get("name", "")).strip()
    if not raw_name:
        return ToolExecutionResult(output="Error: skill name is empty")

    try:
        skill = get_skill_definition(personality, raw_name)
    except ValueError as exc:
        return ToolExecutionResult(output=f"Error: {exc}")
    except FileNotFoundError:
        return ToolExecutionResult(output=f"Error: skill {raw_name!r} not found")

    existing = load_skill_state(memory_root, persona_namespace)
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


def _read_skill_file(
    memory_root: Path,
    persona_namespace: str,
    personality: PersonalityProfile,
    args: dict[str, Any],
) -> ToolExecutionResult:
    state = load_skill_state(memory_root, persona_namespace)
    if state is None:
        return ToolExecutionResult(output="Error: no active skill")

    try:
        skill = get_skill_definition(personality, state.skill_name)
        path = _resolve_resource_path(skill, str(args.get("path", "")))
    except (ValueError, FileNotFoundError) as exc:
        return ToolExecutionResult(output=f"Error: {exc}")

    if not path.is_file():
        return ToolExecutionResult(output=f"Error: file not found: {path.name}")
    return ToolExecutionResult(output=path.read_text(encoding="utf-8"))


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
        return ToolExecutionResult(output=f"Error: {exc}")

    payload: dict[str, Any] = {
        "active": True,
        "skill_name": state.skill_name,
        "status": state.status,
        "skill_type": state.skill_type,
        "step_index": state.step_index,
    }
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
        return ToolExecutionResult(output="Error: no active skill to advance")

    try:
        skill = get_skill_definition(personality, state.skill_name)
    except (FileNotFoundError, ValueError) as exc:
        return ToolExecutionResult(output=f"Error: {exc}")

    if skill.skill_type != "checklist" or not skill.steps:
        return ToolExecutionResult(output="Error: active skill is not a checklist")

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
        return ToolExecutionResult(output="Error: no active skill to pause")

    if state.status == "paused":
        return ToolExecutionResult(output=f"Skill {state.skill_name!r} is already paused.")

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
    state = load_skill_state(memory_root, persona_namespace)
    if state is None:
        return ToolExecutionResult(output="Error: no active skill to cancel")

    clear_skill_state(memory_root, persona_namespace)
    return ToolExecutionResult(
        output=f"Cancelled skill {state.skill_name!r}.",
        refresh_instructions=True,
    )
