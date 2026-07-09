"""Merge voice-friendly params into pulse session.yaml."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from buddy_tools.pulse.schema import (
    SESSION_FILENAME,
    SessionConfig,
    SessionValidationError,
    parse_session_config,
    session_yaml_path,
)
from buddy_tools.pulse.template import render_session_template

# Supported update_pulse_config keys and their session.yaml targets (for docs and validation).
PULSE_CONFIG_PARAM_DOCS: dict[str, str] = {
    "camera_switch_interval_s": "init.set.switch_interval_s",
    "cameras": "cameras",
    "conversation_min_silence_s": "pulse.conversation_check_s",
    "min_speak_interval_s": "pulse.min_speak_interval_s",
    "tick_interval_s": "pulse.tick_interval_s",
    "mandatory_cue_max_defer_s": "pulse.mandatory_cue_max_defer_s",
}

PULSE_CONFIG_PARAM_KEYS = frozenset(PULSE_CONFIG_PARAM_DOCS)


def _ensure_mapping(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key)
    if value is None:
        nested: dict[str, Any] = {}
        raw[key] = nested
        return nested
    if not isinstance(value, dict):
        raise SessionValidationError(f"{key} must be a mapping")
    return value


def merge_pulse_params(raw: dict[str, Any], params: dict[str, Any]) -> list[str]:
    """Apply known params to a session.yaml dict in place. Returns changed param keys."""
    if not isinstance(params, dict):
        raise SessionValidationError("params must be a JSON object")

    unknown = sorted(set(params) - PULSE_CONFIG_PARAM_KEYS)
    if unknown:
        raise SessionValidationError(
            f"Unknown pulse config param(s): {', '.join(unknown)}. "
            f"Supported: {', '.join(sorted(PULSE_CONFIG_PARAM_KEYS))}"
        )

    changed: list[str] = []
    for key, value in params.items():
        if key == "camera_switch_interval_s":
            init = _ensure_mapping(raw, "init")
            init_set = _ensure_mapping(init, "set")
            init_set["switch_interval_s"] = value
        elif key == "cameras":
            if not isinstance(value, list):
                raise SessionValidationError("cameras must be a list")
            raw["cameras"] = value
        elif key == "conversation_min_silence_s":
            pulse = _ensure_mapping(raw, "pulse")
            pulse["conversation_check_s"] = value
        elif key == "min_speak_interval_s":
            pulse = _ensure_mapping(raw, "pulse")
            pulse["min_speak_interval_s"] = value
        elif key == "tick_interval_s":
            pulse = _ensure_mapping(raw, "pulse")
            pulse["tick_interval_s"] = value
        elif key == "mandatory_cue_max_defer_s":
            pulse = _ensure_mapping(raw, "pulse")
            pulse["mandatory_cue_max_defer_s"] = value
        changed.append(key)
    return changed


def _load_session_raw(skill_directory: Path, *, skill_name: str) -> dict[str, Any]:
    path = session_yaml_path(skill_directory)
    if path.is_file():
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise SessionValidationError(f"Could not read {path}: {exc}") from exc
        if raw is None:
            return {}
        if not isinstance(raw, dict):
            raise SessionValidationError(f"{SESSION_FILENAME} root must be a mapping")
        return raw

    name = skill_name or skill_directory.name
    try:
        seeded = yaml.safe_load(render_session_template(name))
    except yaml.YAMLError as exc:
        raise SessionValidationError(f"Could not parse session template: {exc}") from exc
    if not isinstance(seeded, dict):
        raise SessionValidationError("session template must be a mapping")
    return seeded


def apply_pulse_config(skill_directory: Path, params: dict[str, Any], *, skill_name: str = "") -> SessionConfig:
    """Merge params into references/session.yaml, validate, and write back."""
    raw = _load_session_raw(skill_directory, skill_name=skill_name or skill_directory.name)
    changed = merge_pulse_params(raw, params)
    if not changed:
        raise SessionValidationError("params must include at least one supported key")

    name = skill_name or skill_directory.name
    if "name" not in raw or not str(raw.get("name", "")).strip():
        raw["name"] = name

    config = parse_session_config(raw, skill_name=name)
    path = session_yaml_path(skill_directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(raw, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
    return config
