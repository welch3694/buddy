"""Declarative rule and schedule evaluation for pulse sessions.

session.yaml syntax: see SESSION_YAML.md in this package.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any

from buddy_tools.pulse.schema import RuleDefinition, ScheduleEntry, SessionConfig
from buddy_tools.pulse.state import CuePriority, PulseState

logger = logging.getLogger(__name__)

_ELAPSED_SINCE = re.compile(
    r"^elapsed_since\((?P<field>[a-zA-Z_][a-zA-Z0-9_]*)\)\s*>=\s*(?P<threshold>[\d.]+|[a-zA-Z_][a-zA-Z0-9_]*)$"
)
_SESSION_ELAPSED = re.compile(
    r"^session_elapsed\s*>=\s*(?P<threshold>[\d.]+|[a-zA-Z_][a-zA-Z0-9_]*)$"
)
_PHASE_EQ = re.compile(r"^phase\s*==\s*(?P<value>.+)$")
_FIELD_EQ = re.compile(r"^(?P<field>[a-zA-Z_][a-zA-Z0-9_]*)\s*==\s*(?P<value>.+)$")
_FIELD_GTE = re.compile(
    r"^(?P<field>[a-zA-Z_][a-zA-Z0-9_]*)\s*>=\s*(?P<threshold>[\d.]+|[a-zA-Z_][a-zA-Z0-9_]*)$"
)
_FIELD_LTE = re.compile(
    r"^(?P<field>[a-zA-Z_][a-zA-Z0-9_]*)\s*<=\s*(?P<threshold>[\d.]+|[a-zA-Z_][a-zA-Z0-9_]*)$"
)
_MUTATION_CALL = re.compile(r"^\$(?P<name>[a-z_]+)\((?P<args>.*)\)$")


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


def _resolve_numeric_threshold(state: PulseState, threshold: str) -> float | None:
    cleaned = threshold.strip()
    try:
        return float(cleaned)
    except ValueError:
        pass

    value = _lookup_field(state, cleaned)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def evaluate_condition(state: PulseState, condition: str, *, now: datetime | None = None) -> bool:
    cleaned = condition.strip()
    if not cleaned:
        return False
    current = now or datetime.now(UTC)

    if "&&" in cleaned:
        parts = [part.strip() for part in cleaned.split("&&")]
        if not parts or any(not part for part in parts):
            return False
        return all(_evaluate_atomic_condition(state, part, now=current) for part in parts)

    return _evaluate_atomic_condition(state, cleaned, now=current)


def _evaluate_atomic_condition(state: PulseState, condition: str, *, now: datetime) -> bool:
    cleaned = condition.strip()
    if not cleaned:
        return False

    match = _ELAPSED_SINCE.match(cleaned)
    if match:
        elapsed = _elapsed_seconds(state, match.group("field"), now=now)
        threshold = _resolve_numeric_threshold(state, match.group("threshold"))
        if elapsed is None or threshold is None:
            return False
        return elapsed >= threshold

    match = _SESSION_ELAPSED.match(cleaned)
    if match:
        threshold = _resolve_numeric_threshold(state, match.group("threshold"))
        if threshold is None:
            return False
        return _session_elapsed_seconds(state, now=now) >= threshold

    match = _PHASE_EQ.match(cleaned)
    if match:
        return str(state.phase) == _parse_literal(match.group("value"))

    match = _FIELD_GTE.match(cleaned)
    if match:
        value = _lookup_field(state, match.group("field"))
        threshold = _resolve_numeric_threshold(state, match.group("threshold"))
        if value is None or threshold is None:
            return False
        try:
            return float(value) >= threshold
        except (TypeError, ValueError):
            return False

    match = _FIELD_LTE.match(cleaned)
    if match:
        value = _lookup_field(state, match.group("field"))
        threshold = _resolve_numeric_threshold(state, match.group("threshold"))
        if value is None or threshold is None:
            return False
        try:
            return float(value) <= threshold
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


def _split_mutation_args(args: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for char in args:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return parts


def _resolve_mutation_arg(
    raw: str,
    state: PulseState,
    session: SessionConfig,
    *,
    now_iso: str | None = None,
) -> Any:
    cleaned = raw.strip()
    if not cleaned:
        raise ValueError("empty mutation argument")
    if cleaned.startswith("$"):
        return resolve_mutation(cleaned, state, session, now_iso=now_iso)
    try:
        if "." in cleaned:
            return float(cleaned)
        return int(cleaned)
    except ValueError:
        pass
    value = _lookup_field(state, cleaned)
    if value is None:
        raise ValueError(f"unknown mutation argument: {cleaned!r}")
    return value


def _resolve_numeric_arg(
    raw: str,
    state: PulseState,
    session: SessionConfig,
    *,
    now_iso: str | None = None,
) -> float:
    value = _resolve_mutation_arg(raw, state, session, now_iso=now_iso)
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"expected numeric argument, got {value!r}") from exc


def _apply_numeric_mutation(
    name: str,
    args: list[str],
    state: PulseState,
    session: SessionConfig,
    *,
    now_iso: str | None = None,
) -> float:
    if name == "add":
        if len(args) != 2:
            raise ValueError("$add requires exactly 2 arguments")
        return _resolve_numeric_arg(args[0], state, session, now_iso=now_iso) + _resolve_numeric_arg(
            args[1], state, session, now_iso=now_iso
        )

    if name == "sub":
        if len(args) != 2:
            raise ValueError("$sub requires exactly 2 arguments")
        return _resolve_numeric_arg(args[0], state, session, now_iso=now_iso) - _resolve_numeric_arg(
            args[1], state, session, now_iso=now_iso
        )

    if name == "min":
        if len(args) != 2:
            raise ValueError("$min requires exactly 2 arguments")
        return min(
            _resolve_numeric_arg(args[0], state, session, now_iso=now_iso),
            _resolve_numeric_arg(args[1], state, session, now_iso=now_iso),
        )

    if name == "max":
        if len(args) != 2:
            raise ValueError("$max requires exactly 2 arguments")
        return max(
            _resolve_numeric_arg(args[0], state, session, now_iso=now_iso),
            _resolve_numeric_arg(args[1], state, session, now_iso=now_iso),
        )

    if name == "clamp":
        if len(args) == 2:
            value = _resolve_numeric_arg(args[0], state, session, now_iso=now_iso)
            lower = _resolve_numeric_arg(args[1], state, session, now_iso=now_iso)
            return max(lower, value)
        if len(args) == 3:
            value = _resolve_numeric_arg(args[0], state, session, now_iso=now_iso)
            lower = _resolve_numeric_arg(args[1], state, session, now_iso=now_iso)
            upper = _resolve_numeric_arg(args[2], state, session, now_iso=now_iso)
            return min(max(value, lower), upper)
        raise ValueError("$clamp requires 2 arguments (floor) or 3 arguments (floor and ceiling)")

    raise ValueError(f"unsupported mutation: ${name}(...)")

def resolve_mutation(
    value: Any,
    state: PulseState,
    session: SessionConfig,
    *,
    now_iso: str | None = None,
) -> Any:
    if not isinstance(value, str):
        return value
    cleaned = value.strip()
    if cleaned == "$now":
        return now_iso if now_iso is not None else utc_now_iso()

    if not cleaned.startswith("$"):
        return value

    match = _MUTATION_CALL.match(cleaned)
    if not match:
        logger.warning("Unsupported pulse mutation: %r", cleaned)
        return value

    name = match.group("name")
    args = _split_mutation_args(match.group("args"))

    if name == "rotate":
        if len(args) != 1:
            raise ValueError("$rotate requires exactly 1 argument")
        list_name = args[0].strip()
        items = _resolve_list(list_name, state, session)
        current_key = state.vars.get("current_camera") if list_name == "cameras" else state.vars.get(list_name)
        return _rotate_list(items, current_key)

    try:
        numeric = _apply_numeric_mutation(name, args, state, session, now_iso=now_iso)
    except ValueError as exc:
        logger.warning("Pulse mutation failed for %r: %s", cleaned, exc)
        return value

    if numeric.is_integer():
        return int(numeric)
    return numeric


def apply_set_fields(
    state: PulseState,
    session: SessionConfig,
    set_fields: dict[str, Any],
    *,
    now_iso: str | None = None,
) -> None:
    """Apply init.set or rule set mutations in declaration order."""
    for field, raw_value in set_fields.items():
        resolved = resolve_mutation(raw_value, state, session, now_iso=now_iso)
        if field == "phase":
            state.phase = str(resolved)
        else:
            state.vars[field] = resolved


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


_MANDATORY_CUE_SEPARATOR = "; "


def _split_pending_cues(pending_cue: str) -> list[str]:
    return [part.strip() for part in pending_cue.split(";") if part.strip()]


def _merge_mandatory_cues(existing: str, new_cue: str) -> str:
    """Append a mandatory cue, skipping duplicate directive text."""
    new_cue = new_cue.strip()
    if not new_cue:
        return existing
    parts = _split_pending_cues(existing)
    if new_cue in parts:
        return existing
    parts.append(new_cue)
    return _MANDATORY_CUE_SEPARATOR.join(parts)


def _queue_pulse_cue(
    state: PulseState,
    cue_text: str,
    priority: CuePriority,
    *,
    now_iso: str | None = None,
) -> None:
    cleaned = cue_text.strip()
    if not cleaned:
        return

    timestamp = now_iso or utc_now_iso()
    existing = (state.pending_cue or "").strip()
    existing_priority = state.cue_priority

    if priority == "mandatory":
        if existing and existing_priority == "mandatory":
            merged = _merge_mandatory_cues(existing, cleaned)
            state.pending_cue = merged
        else:
            if state.pending_cue != cleaned:
                state.pending_cue_since = timestamp
            state.pending_cue = cleaned
            # Fresh mandatory batch — deferral/fold flag is re-evaluated by gates.
            state.fold_on_next_reply = False
        state.cue_priority = "mandatory"
        state.pulse_mode = "directed"
        return

    if existing and existing_priority == "mandatory":
        return

    if state.pending_cue != cleaned:
        state.pending_cue_since = timestamp
    state.pending_cue = cleaned
    state.cue_priority = "conversational"
    state.pulse_mode = "conversational"
    state.fold_on_next_reply = False


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

    apply_set_fields(state, session, rule.set_fields)

    if rule.cue:
        cue_text = interpolate_template(rule.cue, state, session)
        _queue_pulse_cue(state, cue_text, rule.priority)

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

    cue_text = interpolate_template(entry.cue, state, session)
    _queue_pulse_cue(state, cue_text, entry.priority)
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
