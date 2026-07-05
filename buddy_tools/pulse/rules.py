"""Declarative rule and schedule evaluation for pulse sessions."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any

from buddy_tools.pulse.schema import RuleDefinition, ScheduleEntry, SessionConfig
from buddy_tools.pulse.state import PulseState

logger = logging.getLogger(__name__)

_ELAPSED_SINCE = re.compile(
    r"^elapsed_since\((?P<field>[a-zA-Z_][a-zA-Z0-9_]*)\)\s*>=\s*(?P<threshold>[\d.]+)$"
)
_PHASE_EQ = re.compile(r"^phase\s*==\s*(?P<value>.+)$")
_FIELD_EQ = re.compile(r"^(?P<field>[a-zA-Z_][a-zA-Z0-9_]*)\s*==\s*(?P<value>.+)$")
_FIELD_GTE = re.compile(r"^(?P<field>[a-zA-Z_][a-zA-Z0-9_]*)\s*>=\s*(?P<threshold>[\d.]+)$")
_FIELD_LTE = re.compile(r"^(?P<field>[a-zA-Z_][a-zA-Z0-9_]*)\s*<=\s*(?P<threshold>[\d.]+)$")
_ROTATE = re.compile(r"^\$rotate\((?P<list_name>[a-zA-Z_][a-zA-Z0-9_]*)\)$")


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _parse_iso_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=UTC)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _parse_literal(value: str) -> Any:
    cleaned = value.strip()
    if cleaned.lower() in ("true", "false"):
        return cleaned.lower() == "true"
    if cleaned.startswith('"') and cleaned.endswith('"'):
        return cleaned[1:-1]
    if cleaned.startswith("'") and cleaned.endswith("'"):
        return cleaned[1:-1]
    try:
        if "." in cleaned:
            return float(cleaned)
        return int(cleaned)
    except ValueError:
        return cleaned


def _lookup_field(state: PulseState, field: str) -> Any:
    if field == "phase":
        return state.phase
    return state.vars.get(field)


def _elapsed_seconds(state: PulseState, field: str, *, now: datetime) -> float | None:
    if field == "started_at":
        anchor = _parse_iso_timestamp(state.started_at)
    else:
        anchor = _parse_iso_timestamp(state.vars.get(field))
    if anchor is None:
        return None
    return max(0.0, (now - anchor).total_seconds())


def _session_elapsed_seconds(state: PulseState, *, now: datetime) -> float:
    started = _parse_iso_timestamp(state.started_at)
    if started is None:
        return 0.0
    return max(0.0, (now - started).total_seconds())


def evaluate_condition(state: PulseState, condition: str, *, now: datetime | None = None) -> bool:
    cleaned = condition.strip()
    if not cleaned:
        return False
    current = now or datetime.now(UTC)

    match = _ELAPSED_SINCE.match(cleaned)
    if match:
        elapsed = _elapsed_seconds(state, match.group("field"), now=current)
        if elapsed is None:
            return False
        return elapsed >= float(match.group("threshold"))

    match = _PHASE_EQ.match(cleaned)
    if match:
        return str(state.phase) == _parse_literal(match.group("value"))

    match = _FIELD_GTE.match(cleaned)
    if match:
        value = _lookup_field(state, match.group("field"))
        if value is None:
            return False
        try:
            return float(value) >= float(match.group("threshold"))
        except (TypeError, ValueError):
            return False

    match = _FIELD_LTE.match(cleaned)
    if match:
        value = _lookup_field(state, match.group("field"))
        if value is None:
            return False
        try:
            return float(value) <= float(match.group("threshold"))
        except (TypeError, ValueError):
            return False

    match = _FIELD_EQ.match(cleaned)
    if match:
        field = match.group("field")
        expected = _parse_literal(match.group("value"))
        actual = _lookup_field(state, field)
        return actual == expected

    logger.warning("Unsupported pulse rule condition: %r", cleaned)
    return False


def _resolve_list(list_name: str, state: PulseState, session: SessionConfig) -> list[Any]:
    if list_name == "cameras":
        return list(session.cameras)
    value = state.vars.get(list_name)
    if isinstance(value, list):
        return value
    return []


def _rotate_list(items: list[Any], current: Any) -> Any:
    if not items:
        return current

    keys: list[Any] = []
    for item in items:
        if isinstance(item, dict) and "id" in item:
            keys.append(item["id"])
        else:
            keys.append(item)

    if current not in keys:
        return keys[0]
    index = keys.index(current)
    return keys[(index + 1) % len(keys)]


def resolve_mutation(value: Any, state: PulseState, session: SessionConfig) -> Any:
    if not isinstance(value, str):
        return value
    if value == "$now":
        return utc_now_iso()

    match = _ROTATE.match(value.strip())
    if match:
        list_name = match.group("list_name")
        items = _resolve_list(list_name, state, session)
        current_key = state.vars.get("current_camera") if list_name == "cameras" else state.vars.get(list_name)
        return _rotate_list(items, current_key)

    return value


def _camera_label(session: SessionConfig, camera_id: Any) -> str:
    for camera in session.cameras:
        if isinstance(camera, dict) and camera.get("id") == camera_id:
            return str(camera.get("label", ""))
    return ""


def interpolate_template(template: str, state: PulseState, session: SessionConfig) -> str:
    context: dict[str, Any] = dict(state.vars)
    context["phase"] = state.phase
    if "current_camera" in context:
        context.setdefault("label", _camera_label(session, context["current_camera"]))

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        value = context.get(key, "")
        return "" if value is None else str(value)

    return re.sub(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", replace, template)


def apply_rule(
    state: PulseState,
    rule: RuleDefinition,
    session: SessionConfig,
    *,
    now: datetime | None = None,
) -> bool:
    if rule.once and rule.id in state.fired_rules:
        return False
    if not evaluate_condition(state, rule.when, now=now):
        return False

    for field, raw_value in rule.set_fields.items():
        resolved = resolve_mutation(raw_value, state, session)
        if field == "phase":
            state.phase = str(resolved)
        else:
            state.vars[field] = resolved

    if rule.cue:
        cue_text = interpolate_template(rule.cue, state, session)
        if cue_text.strip():
            if state.pending_cue != cue_text:
                state.pending_cue_since = utc_now_iso()
            state.pending_cue = cue_text
            state.cue_priority = rule.priority
            state.pulse_mode = "directed" if rule.priority == "mandatory" else "conversational"

    if rule.once:
        state.fired_rules.append(rule.id)

    logger.info(
        "Pulse rule fired: id=%r skill=%r pending_cue=%r",
        rule.id,
        state.skill_name,
        state.pending_cue,
    )
    return True


def apply_schedule_entry(
    state: PulseState,
    entry: ScheduleEntry,
    session: SessionConfig,
    *,
    elapsed_s: float,
) -> bool:
    fired_key = f"schedule:{entry.entry_id}"
    if fired_key in state.fired_rules:
        return False
    if elapsed_s < entry.at_s:
        return False

    state.pending_cue = interpolate_template(entry.cue, state, session)
    state.cue_priority = entry.priority
    state.pulse_mode = "directed" if entry.priority == "mandatory" else "conversational"
    state.pending_cue_since = utc_now_iso()
    state.fired_rules.append(fired_key)
    logger.info(
        "Pulse schedule fired: id=%r skill=%r at_s=%.2f",
        entry.entry_id,
        state.skill_name,
        entry.at_s,
    )
    return True


def evaluate_pulse_tick(state: PulseState, session: SessionConfig) -> PulseState:
    """Run schedule and rule evaluation for one worker tick."""
    now = datetime.now(UTC)
    elapsed_s = _session_elapsed_seconds(state, now=now)

    for entry in session.schedule:
        apply_schedule_entry(state, entry, session, elapsed_s=elapsed_s)

    for rule in session.rules:
        apply_rule(state, rule, session, now=now)

    if "narrator_muted" in state.vars:
        state.narrator_muted = bool(state.vars.get("narrator_muted"))

    return state
