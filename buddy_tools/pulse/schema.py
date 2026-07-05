"""session.yaml schema validation and parsing for pulse skills."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

logger = logging.getLogger(__name__)

SESSION_FILENAME = "session.yaml"
RulePriority = Literal["mandatory", "conversational"]


@dataclass(frozen=True)
class PulseTimingConfig:
    tick_interval_s: float = 5.0
    conversation_check_s: float | None = None
    min_speak_interval_s: float | None = None
    mandatory_cue_max_defer_s: float | None = None


@dataclass(frozen=True)
class RuleDefinition:
    id: str
    when: str
    set_fields: dict[str, Any] = field(default_factory=dict)
    cue: str | None = None
    priority: RulePriority = "mandatory"
    once: bool = False


@dataclass(frozen=True)
class ScheduleEntry:
    at_s: float
    cue: str
    priority: RulePriority = "mandatory"
    entry_id: str = ""


@dataclass(frozen=True)
class SessionConfig:
    name: str
    pulse: PulseTimingConfig
    init_set: dict[str, Any] = field(default_factory=dict)
    cameras: tuple[Any, ...] = ()
    rules: tuple[RuleDefinition, ...] = ()
    schedule: tuple[ScheduleEntry, ...] = ()


class SessionValidationError(ValueError):
    """Raised when session.yaml fails schema validation."""


def session_yaml_path(skill_directory: Path) -> Path:
    return skill_directory / "references" / SESSION_FILENAME


def _positive_float(value: Any, field_name: str) -> float:
    if not isinstance(value, (int, float)):
        raise SessionValidationError(f"{field_name} must be a number")
    numeric = float(value)
    if numeric <= 0:
        raise SessionValidationError(f"{field_name} must be positive")
    return numeric


def _optional_positive_float(value: Any, field_name: str) -> float | None:
    if value is None:
        return None
    return _positive_float(value, field_name)


def _normalize_raw_session(raw: dict[str, Any]) -> dict[str, Any]:
    """Accept legacy flat keys from early pulse prototypes."""
    normalized = dict(raw)
    pulse = normalized.get("pulse")
    if not isinstance(pulse, dict):
        pulse = {}
        normalized["pulse"] = pulse

    if "tick_interval_s" not in pulse and "tick_interval_seconds" in normalized:
        pulse["tick_interval_s"] = normalized.pop("tick_interval_seconds")
    elif "tick_interval_s" not in pulse and "tick_interval_seconds" in pulse:
        pulse["tick_interval_s"] = pulse.pop("tick_interval_seconds")

    init = normalized.get("init")
    if not isinstance(init, dict):
        init = {}
        normalized["init"] = init
    init_set = init.get("set")
    if not isinstance(init_set, dict):
        init_set = {}
        init["set"] = init_set

    if "phase" in normalized and "phase" not in init_set:
        init_set["phase"] = normalized.pop("phase")

    return normalized


def _parse_pulse_config(raw: dict[str, Any]) -> PulseTimingConfig:
    pulse_raw = raw.get("pulse", {})
    if not isinstance(pulse_raw, dict):
        raise SessionValidationError("pulse must be a mapping")

    tick_interval = pulse_raw.get("tick_interval_s", 5.0)
    return PulseTimingConfig(
        tick_interval_s=_positive_float(tick_interval, "pulse.tick_interval_s"),
        conversation_check_s=_optional_positive_float(
            pulse_raw.get("conversation_check_s"),
            "pulse.conversation_check_s",
        ),
        min_speak_interval_s=_optional_positive_float(
            pulse_raw.get("min_speak_interval_s"),
            "pulse.min_speak_interval_s",
        ),
        mandatory_cue_max_defer_s=_optional_positive_float(
            pulse_raw.get("mandatory_cue_max_defer_s"),
            "pulse.mandatory_cue_max_defer_s",
        ),
    )


def _parse_rule(entry: Any, index: int) -> RuleDefinition:
    if not isinstance(entry, dict):
        raise SessionValidationError(f"rules[{index}] must be a mapping")

    rule_id = str(entry.get("id", "")).strip()
    if not rule_id:
        raise SessionValidationError(f"rules[{index}] requires id")

    when = str(entry.get("when", "")).strip()
    if not when:
        raise SessionValidationError(f"rules[{index}] requires when")

    set_raw = entry.get("set", {})
    if set_raw is None:
        set_raw = {}
    if not isinstance(set_raw, dict):
        raise SessionValidationError(f"rules[{rule_id}].set must be a mapping")

    priority = str(entry.get("priority", "mandatory")).strip().lower()
    if priority not in ("mandatory", "conversational"):
        raise SessionValidationError(
            f"rules[{rule_id}].priority must be 'mandatory' or 'conversational'"
        )

    cue_raw = entry.get("cue")
    cue = None if cue_raw is None else str(cue_raw)

    return RuleDefinition(
        id=rule_id,
        when=when,
        set_fields=dict(set_raw),
        cue=cue,
        priority=priority,  # type: ignore[arg-type]
        once=bool(entry.get("once", False)),
    )


def _parse_schedule(entry: Any, index: int) -> ScheduleEntry:
    if not isinstance(entry, dict):
        raise SessionValidationError(f"schedule[{index}] must be a mapping")

    cue = str(entry.get("cue", "")).strip()
    if not cue:
        raise SessionValidationError(f"schedule[{index}] requires cue")

    at_s = entry.get("at_s")
    if at_s is None:
        raise SessionValidationError(f"schedule[{index}] requires at_s")

    priority = str(entry.get("priority", "mandatory")).strip().lower()
    if priority not in ("mandatory", "conversational"):
        raise SessionValidationError(
            f"schedule[{index}].priority must be 'mandatory' or 'conversational'"
        )

    entry_id = str(entry.get("id", "")).strip() or f"schedule-{at_s}"

    return ScheduleEntry(
        at_s=_positive_float(at_s, f"schedule[{index}].at_s"),
        cue=cue,
        priority=priority,  # type: ignore[arg-type]
        entry_id=entry_id,
    )


def parse_session_config(raw: dict[str, Any], *, skill_name: str = "") -> SessionConfig:
    if not isinstance(raw, dict):
        raise SessionValidationError("session.yaml root must be a mapping")

    normalized = _normalize_raw_session(raw)
    name = str(normalized.get("name", skill_name)).strip() or skill_name
    if not name:
        raise SessionValidationError("session.yaml requires name")

    init = normalized.get("init", {})
    init_set: dict[str, Any] = {}
    if isinstance(init, dict):
        raw_set = init.get("set", {})
        if raw_set is not None:
            if not isinstance(raw_set, dict):
                raise SessionValidationError("init.set must be a mapping")
            init_set = dict(raw_set)

    cameras_raw = normalized.get("cameras", [])
    if cameras_raw is None:
        cameras_raw = []
    if not isinstance(cameras_raw, list):
        raise SessionValidationError("cameras must be a list")

    rules_raw = normalized.get("rules", [])
    if rules_raw is None:
        rules_raw = []
    if not isinstance(rules_raw, list):
        raise SessionValidationError("rules must be a list")

    schedule_raw = normalized.get("schedule", [])
    if schedule_raw is None:
        schedule_raw = []
    if not isinstance(schedule_raw, list):
        raise SessionValidationError("schedule must be a list")

    return SessionConfig(
        name=name,
        pulse=_parse_pulse_config(normalized),
        init_set=init_set,
        cameras=tuple(cameras_raw),
        rules=tuple(_parse_rule(entry, index) for index, entry in enumerate(rules_raw)),
        schedule=tuple(
            _parse_schedule(entry, index) for index, entry in enumerate(schedule_raw)
        ),
    )


def session_config_to_dict(config: SessionConfig) -> dict[str, Any]:
    return {
        "name": config.name,
        "pulse": {
            "tick_interval_s": config.pulse.tick_interval_s,
            **{
                key: value
                for key, value in (
                    ("conversation_check_s", config.pulse.conversation_check_s),
                    ("min_speak_interval_s", config.pulse.min_speak_interval_s),
                    ("mandatory_cue_max_defer_s", config.pulse.mandatory_cue_max_defer_s),
                )
                if value is not None
            },
        },
        "init": {"set": dict(config.init_set)},
        "cameras": list(config.cameras),
        "rules": [
            {
                "id": rule.id,
                "when": rule.when,
                "set": dict(rule.set_fields),
                **({"cue": rule.cue} if rule.cue is not None else {}),
                "priority": rule.priority,
                "once": rule.once,
            }
            for rule in config.rules
        ],
        "schedule": [
            {
                "at_s": entry.at_s,
                "cue": entry.cue,
                "priority": entry.priority,
                **({"id": entry.entry_id} if entry.entry_id else {}),
            }
            for entry in config.schedule
        ],
    }


def session_config_from_dict(data: dict[str, Any]) -> SessionConfig:
    return parse_session_config(data, skill_name=str(data.get("name", "")))


def load_session_config(skill_directory: Path, *, skill_name: str = "") -> SessionConfig:
    path = session_yaml_path(skill_directory)
    if not path.is_file():
        raise SessionValidationError(f"Missing {SESSION_FILENAME} at {path}")

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise SessionValidationError(f"Could not read {path}: {exc}") from exc

    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise SessionValidationError(f"{SESSION_FILENAME} root must be a mapping")

    return parse_session_config(raw, skill_name=skill_name or skill_directory.name)


def try_load_session_config(
    skill_directory: Path,
    *,
    skill_name: str = "",
) -> SessionConfig | None:
    try:
        return load_session_config(skill_directory, skill_name=skill_name)
    except SessionValidationError as exc:
        logger.warning("Invalid session.yaml in %s: %s", skill_directory, exc)
        return None
