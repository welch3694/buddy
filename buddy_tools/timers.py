"""In-process timer scheduler for proactive agent turns."""

from __future__ import annotations

import json
import logging
import random
import threading
import time
from dataclasses import dataclass
from queue import Queue
from threading import Event, Lock, Timer
from typing import Any, Callable, Literal

from openai.types.realtime import RealtimeFunctionTool
from openai.types.realtime.realtime_response_create_params import RealtimeResponseCreateParams
from speech_to_speech.api.openai_realtime.runtime_config import RuntimeConfig
from speech_to_speech.LLM.chat import make_user_message
from speech_to_speech.pipeline.messages import GenerateResponseRequest

from buddy_tools.listening_pause import get_listening_pause_controller
from buddy_tools.result import ToolExecutionResult

logger = logging.getLogger(__name__)

TimerMode = Literal["once", "repeat"]
TimerGate = Literal["wall_clock", "after_silence"]

DEFER_POLL_SECONDS = 0.25
MIN_ARM_DELAY_SECONDS = 0.05
TIMER_NUDGE_PREFIX = "[Timer — internal scheduled nudge, not user speech]: "

_perf_counter_fn: Callable[[], float] = time.perf_counter
_last_user_speech_stopped_at_s: float | None = None
_scheduler: TimerScheduler | None = None


def _perf_counter() -> float:
    return _perf_counter_fn()


def set_perf_counter_for_tests(fn: Callable[[], float] | None) -> None:
    global _perf_counter_fn
    _perf_counter_fn = fn or time.perf_counter


@dataclass(frozen=True)
class TimerConfig:
    id: str
    prompt: str
    mode: TimerMode
    gate: TimerGate
    delay_seconds: float | None = None
    interval_seconds: float | None = None
    interval_min_seconds: float | None = None
    interval_max_seconds: float | None = None
    defer_while_busy: bool = True
    cancel_on_user_speech: bool = False
    skill_name: str | None = None


@dataclass
class ActiveTimer:
    config: TimerConfig
    armed_at_s: float = 0.0
    next_fire_at_s: float = 0.0
    generation: int = 0
    _handle: Timer | None = None


class TimerScheduler:
    """Thread-safe registry of active timers that inject system nudges into the pipeline."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._timers: dict[str, ActiveTimer] = {}
        self.text_prompt_queue: Queue[Any] | None = None
        self.runtime_config: RuntimeConfig | None = None
        self.should_listen: Event | None = None

    def configure(
        self,
        *,
        text_prompt_queue: Queue[Any] | None,
        runtime_config: RuntimeConfig | None,
        should_listen: Event | None,
    ) -> None:
        with self._lock:
            self.text_prompt_queue = text_prompt_queue
            self.runtime_config = runtime_config
            self.should_listen = should_listen

    def start(self, config: TimerConfig, *, replace: bool = False) -> str:
        with self._lock:
            existing = self._timers.get(config.id)
            if existing is not None and not replace:
                raise ValueError(f"Timer {config.id!r} already exists (use replace=true to update)")

            if existing is not None:
                self._cancel_handle(existing)

            active = ActiveTimer(config=config)
            self._timers[config.id] = active
            self._arm(active, is_repeat_arm=False)
            return config.id

    def cancel(self, timer_id: str | None = None) -> int:
        with self._lock:
            if timer_id is None:
                cancelled = list(self._timers.keys())
                for active in self._timers.values():
                    self._cancel_handle(active)
                self._timers.clear()
                return len(cancelled)

            active = self._timers.pop(timer_id, None)
            if active is None:
                return 0
            self._cancel_handle(active)
            return 1

    def cancel_for_skill(self, skill_name: str) -> int:
        with self._lock:
            to_remove = [timer_id for timer_id, active in self._timers.items() if active.config.skill_name == skill_name]
            for timer_id in to_remove:
                active = self._timers.pop(timer_id)
                self._cancel_handle(active)
            return len(to_remove)

    def cancel_on_user_speech(self) -> int:
        with self._lock:
            to_remove = [
                timer_id
                for timer_id, active in self._timers.items()
                if active.config.cancel_on_user_speech
            ]
            for timer_id in to_remove:
                active = self._timers.pop(timer_id)
                self._cancel_handle(active)
            return len(to_remove)

    def reschedule(self, timer_id: str, updates: dict[str, Any]) -> str:
        with self._lock:
            active = self._timers.get(timer_id)
            if active is None:
                raise ValueError(f"Timer {timer_id!r} not found")

        merged = _merge_timer_config(active.config, updates)
        return self.start(merged, replace=True)

    def list_active(self) -> list[dict[str, Any]]:
        now = _perf_counter()
        with self._lock:
            items: list[dict[str, Any]] = []
            for active in self._timers.values():
                cfg = active.config
                entry: dict[str, Any] = {
                    "id": cfg.id,
                    "gate": cfg.gate,
                    "mode": cfg.mode,
                    "defer_while_busy": cfg.defer_while_busy,
                    "cancel_on_user_speech": cfg.cancel_on_user_speech,
                }
                if cfg.skill_name:
                    entry["skill_name"] = cfg.skill_name
                if cfg.delay_seconds is not None:
                    entry["delay_seconds"] = cfg.delay_seconds
                if cfg.interval_seconds is not None:
                    entry["interval_seconds"] = cfg.interval_seconds
                if cfg.interval_min_seconds is not None:
                    entry["interval_min_seconds"] = cfg.interval_min_seconds
                if cfg.interval_max_seconds is not None:
                    entry["interval_max_seconds"] = cfg.interval_max_seconds
                seconds_until_next = max(0.0, active.next_fire_at_s - now)
                entry["seconds_until_next"] = round(seconds_until_next, 2)
                items.append(entry)
            return items

    def _arm(self, active: ActiveTimer, *, is_repeat_arm: bool) -> None:
        delay = self._compute_arm_delay(active.config, is_repeat_arm=is_repeat_arm)
        now = _perf_counter()
        active.armed_at_s = now
        active.next_fire_at_s = now + delay
        active.generation += 1
        generation = active.generation
        timer_id = active.config.id

        self._cancel_handle(active)
        handle = Timer(delay, self._on_wake, args=(timer_id, generation))
        handle.daemon = True
        active._handle = handle
        handle.start()

    def _compute_arm_delay(self, config: TimerConfig, *, is_repeat_arm: bool) -> float:
        if is_repeat_arm and config.mode == "repeat":
            chosen = _choose_interval_seconds(config, for_repeat=True)
        else:
            chosen = _choose_interval_seconds(config, for_repeat=False)

        if config.gate == "after_silence":
            silence_delay = config.delay_seconds
            if silence_delay is None:
                silence_delay = chosen
            last_speech = _last_user_speech_stopped_at_s
            now = _perf_counter()
            if last_speech is None:
                target = now + silence_delay
            else:
                target = max(now + MIN_ARM_DELAY_SECONDS, last_speech + silence_delay)
            return max(MIN_ARM_DELAY_SECONDS, target - now)

        return max(MIN_ARM_DELAY_SECONDS, chosen)

    def _on_wake(self, timer_id: str, generation: int) -> None:
        with self._lock:
            active = self._timers.get(timer_id)
            if active is None or active.generation != generation:
                return
            config = active.config

        if not self._gates_allow_fire(config):
            with self._lock:
                active = self._timers.get(timer_id)
                if active is None or active.generation != generation:
                    return
                active.next_fire_at_s = _perf_counter() + DEFER_POLL_SECONDS
                active.generation += 1
                new_generation = active.generation
                self._cancel_handle(active)
                handle = Timer(DEFER_POLL_SECONDS, self._on_wake, args=(timer_id, new_generation))
                handle.daemon = True
                active._handle = handle
                handle.start()
            return

        self._inject_tick(config.prompt)

        with self._lock:
            active = self._timers.get(timer_id)
            if active is None or active.generation != generation:
                return
            if config.mode == "once":
                self._cancel_handle(active)
                self._timers.pop(timer_id, None)
                return
            self._arm(active, is_repeat_arm=True)

    def _gates_allow_fire(self, config: TimerConfig) -> bool:
        if get_listening_pause_controller().paused:
            return False

        if config.defer_while_busy and self.should_listen is not None and not self.should_listen.is_set():
            return False

        if config.gate == "after_silence":
            silence_delay = config.delay_seconds
            if silence_delay is None:
                silence_delay = _choose_interval_seconds(config, for_repeat=False)
            last_speech = _last_user_speech_stopped_at_s
            if last_speech is not None and (_perf_counter() - last_speech) < silence_delay:
                return False

        return True

    def _inject_tick(self, prompt: str) -> None:
        runtime_config = self.runtime_config
        text_prompt_queue = self.text_prompt_queue
        if runtime_config is None or text_prompt_queue is None:
            logger.warning("Timer tick dropped: scheduler not configured with runtime_config/queue")
            return

        try:
            runtime_config.chat.add_item(make_user_message(f"{TIMER_NUDGE_PREFIX}{prompt}"))
        except Exception:
            logger.exception("Timer tick failed to add nudge message to chat")
            return

        base_instructions = runtime_config.session.instructions or ""
        tick_instructions = (
            f"{base_instructions}\n\nScheduled timer tick: {prompt}\n"
            "Respond with a fresh utterance that fulfills the timer prompt. "
            "Do not repeat your previous assistant message."
        )

        text_prompt_queue.put(
            GenerateResponseRequest(
                runtime_config=runtime_config,
                response=RealtimeResponseCreateParams(instructions=tick_instructions),
                turn_id=None,
                turn_revision=None,
            )
        )
        logger.info("Timer tick injected into text_prompt_queue")

    @staticmethod
    def _cancel_handle(active: ActiveTimer) -> None:
        handle = active._handle
        if handle is not None:
            handle.cancel()
            active._handle = None


def get_timer_scheduler() -> TimerScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = TimerScheduler()
    return _scheduler


def reset_timer_scheduler_for_tests() -> None:
    global _scheduler, _last_user_speech_stopped_at_s
    if _scheduler is not None:
        _scheduler.cancel()
        _scheduler = None
    _last_user_speech_stopped_at_s = None
    set_perf_counter_for_tests(None)


def configure_timers(
    *,
    text_prompt_queue: Queue[Any] | None,
    runtime_config: RuntimeConfig | None,
    should_listen: Event | None,
) -> TimerScheduler:
    scheduler = get_timer_scheduler()
    scheduler.configure(
        text_prompt_queue=text_prompt_queue,
        runtime_config=runtime_config,
        should_listen=should_listen,
    )
    return scheduler


def cancel_all_timers() -> int:
    return get_timer_scheduler().cancel()


def cancel_timers_for_skill(skill_name: str) -> int:
    return get_timer_scheduler().cancel_for_skill(skill_name)


def notify_user_speech(speech_stopped_at_s: float | None) -> None:
    global _last_user_speech_stopped_at_s
    if speech_stopped_at_s is not None:
        _last_user_speech_stopped_at_s = speech_stopped_at_s
    get_timer_scheduler().cancel_on_user_speech()


def _choose_interval_seconds(config: TimerConfig, *, for_repeat: bool) -> float:
    if for_repeat:
        if config.interval_min_seconds is not None and config.interval_max_seconds is not None:
            low = config.interval_min_seconds
            high = config.interval_max_seconds
            if high < low:
                low, high = high, low
            return random.uniform(low, high)
        if config.interval_seconds is not None:
            return config.interval_seconds
        if config.interval_min_seconds is not None:
            return config.interval_min_seconds
        if config.delay_seconds is not None:
            return config.delay_seconds
        raise ValueError("repeat timer requires interval_seconds or interval_min/max_seconds")

    if config.delay_seconds is not None:
        return config.delay_seconds
    if config.interval_seconds is not None:
        return config.interval_seconds
    if config.interval_min_seconds is not None and config.interval_max_seconds is not None:
        low = config.interval_min_seconds
        high = config.interval_max_seconds
        if high < low:
            low, high = high, low
        return random.uniform(low, high)
    if config.interval_min_seconds is not None:
        return config.interval_min_seconds
    raise ValueError("timer requires delay_seconds, interval_seconds, or interval_min/max_seconds")


def _parse_timer_config(args: dict[str, Any]) -> TimerConfig:
    timer_id = args.get("id")
    prompt = args.get("prompt")
    mode = args.get("mode")
    gate = args.get("gate")

    if not isinstance(timer_id, str) or not timer_id.strip():
        raise ValueError("id is required")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt is required")
    if mode not in ("once", "repeat"):
        raise ValueError("mode must be 'once' or 'repeat'")
    if gate not in ("wall_clock", "after_silence"):
        raise ValueError("gate must be 'wall_clock' or 'after_silence'")

    delay_seconds = _optional_positive_float(args, "delay_seconds")
    interval_seconds = _optional_positive_float(args, "interval_seconds")
    interval_min_seconds = _optional_positive_float(args, "interval_min_seconds")
    interval_max_seconds = _optional_positive_float(args, "interval_max_seconds")

    if gate == "after_silence" and delay_seconds is None and interval_seconds is None:
        if interval_min_seconds is None:
            raise ValueError("after_silence gate requires delay_seconds or interval timing")

    defer_while_busy = args.get("defer_while_busy", True)
    if not isinstance(defer_while_busy, bool):
        raise ValueError("defer_while_busy must be a boolean")

    cancel_on_user_speech = args.get("cancel_on_user_speech", False)
    if not isinstance(cancel_on_user_speech, bool):
        raise ValueError("cancel_on_user_speech must be a boolean")

    skill_name = args.get("skill_name")
    if skill_name is not None and not isinstance(skill_name, str):
        raise ValueError("skill_name must be a string")

    config = TimerConfig(
        id=timer_id.strip(),
        prompt=prompt.strip(),
        mode=mode,
        gate=gate,
        delay_seconds=delay_seconds,
        interval_seconds=interval_seconds,
        interval_min_seconds=interval_min_seconds,
        interval_max_seconds=interval_max_seconds,
        defer_while_busy=defer_while_busy,
        cancel_on_user_speech=cancel_on_user_speech,
        skill_name=skill_name.strip() if isinstance(skill_name, str) and skill_name.strip() else None,
    )
    _choose_interval_seconds(config, for_repeat=(mode == "repeat"))
    return config


def _merge_timer_config(base: TimerConfig, updates: dict[str, Any]) -> TimerConfig:
    merged_args: dict[str, Any] = {
        "id": base.id,
        "prompt": base.prompt,
        "mode": base.mode,
        "gate": base.gate,
        "defer_while_busy": base.defer_while_busy,
        "cancel_on_user_speech": base.cancel_on_user_speech,
    }
    if base.delay_seconds is not None:
        merged_args["delay_seconds"] = base.delay_seconds
    if base.interval_seconds is not None:
        merged_args["interval_seconds"] = base.interval_seconds
    if base.interval_min_seconds is not None:
        merged_args["interval_min_seconds"] = base.interval_min_seconds
    if base.interval_max_seconds is not None:
        merged_args["interval_max_seconds"] = base.interval_max_seconds
    if base.skill_name is not None:
        merged_args["skill_name"] = base.skill_name

    for key, value in updates.items():
        if value is not None:
            merged_args[key] = value

    return _parse_timer_config(merged_args)


def _optional_positive_float(args: dict[str, Any], key: str) -> float | None:
    value = args.get(key)
    if value is None:
        return None
    if not isinstance(value, (int, float)):
        raise ValueError(f"{key} must be a number")
    numeric = float(value)
    if numeric <= 0:
        raise ValueError(f"{key} must be positive")
    return numeric


TIMER_TOOL_DEFINITIONS: list[RealtimeFunctionTool] = [
    RealtimeFunctionTool(
        type="function",
        name="start_timer",
        description=(
            "Schedule a proactive assistant turn on a timer. Use for repeating check-ins, "
            "silence-based conversation nudges, or precise wall-clock interval cues. "
            "Ticks inject an internal system prompt and trigger a new response — not a fake user message."
        ),
        parameters={
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Unique timer name"},
                "prompt": {"type": "string", "description": "System nudge injected on each tick"},
                "mode": {"type": "string", "enum": ["once", "repeat"], "description": "Fire once or repeat"},
                "gate": {
                    "type": "string",
                    "enum": ["wall_clock", "after_silence"],
                    "description": "wall_clock fires on schedule; after_silence waits for user silence",
                },
                "delay_seconds": {"type": "number", "description": "Initial delay or silence duration"},
                "interval_seconds": {"type": "number", "description": "Fixed repeat interval"},
                "interval_min_seconds": {"type": "number", "description": "Jittered repeat minimum"},
                "interval_max_seconds": {"type": "number", "description": "Jittered repeat maximum"},
                "defer_while_busy": {
                    "type": "boolean",
                    "description": "When true, defer ticks while mic pipeline is busy (default true)",
                },
                "cancel_on_user_speech": {
                    "type": "boolean",
                    "description": "When true, cancel if the user speaks before the tick fires",
                },
                "skill_name": {
                    "type": "string",
                    "description": "Associate timer with a skill for auto-cancel on cancel_skill",
                },
                "replace": {
                    "type": "boolean",
                    "description": "When true, atomically replace an existing timer with the same id",
                },
            },
            "required": ["id", "prompt", "mode", "gate"],
        },
    ),
    RealtimeFunctionTool(
        type="function",
        name="cancel_timer",
        description="Cancel one timer by id, or all active timers when id is omitted.",
        parameters={
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Timer id to cancel; omit to cancel all"},
            },
        },
    ),
    RealtimeFunctionTool(
        type="function",
        name="reschedule_timer",
        description=(
            "Update timing or flags on an active timer in place. Re-arms from now with new parameters."
        ),
        parameters={
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Timer id to reschedule"},
                "prompt": {"type": "string"},
                "mode": {"type": "string", "enum": ["once", "repeat"]},
                "gate": {"type": "string", "enum": ["wall_clock", "after_silence"]},
                "delay_seconds": {"type": "number"},
                "interval_seconds": {"type": "number"},
                "interval_min_seconds": {"type": "number"},
                "interval_max_seconds": {"type": "number"},
                "defer_while_busy": {"type": "boolean"},
                "cancel_on_user_speech": {"type": "boolean"},
                "skill_name": {"type": "string"},
            },
            "required": ["id"],
        },
    ),
    RealtimeFunctionTool(
        type="function",
        name="list_timers",
        description="List active timers with gate, mode, interval config, and seconds_until_next estimate.",
        parameters={"type": "object", "properties": {}},
    ),
]

TIMER_TOOL_NAMES = frozenset(tool.name for tool in TIMER_TOOL_DEFINITIONS)


def build_timer_instructions() -> str:
    return (
        "Timer tools schedule proactive assistant turns without fake user messages. "
        "Use start_timer for repeating check-ins or interval coaching; cancel_timer to stop; "
        "list_timers to report pace; reschedule_timer or start_timer with replace=true to change cadence atomically."
    )


def execute_timer_tool(tool_name: str, args: dict[str, Any]) -> ToolExecutionResult:
    scheduler = get_timer_scheduler()

    if tool_name == "start_timer":
        replace = bool(args.get("replace", False))
        timer_args = {k: v for k, v in args.items() if k != "replace"}
        config = _parse_timer_config(timer_args)
        timer_id = scheduler.start(config, replace=replace)
        return ToolExecutionResult(output=f"Started timer {timer_id!r} ({config.mode}, {config.gate}).")

    if tool_name == "cancel_timer":
        timer_id = args.get("id")
        if timer_id is not None and not isinstance(timer_id, str):
            raise ValueError("id must be a string")
        count = scheduler.cancel(timer_id.strip() if isinstance(timer_id, str) else None)
        if timer_id:
            if count:
                return ToolExecutionResult(output=f"Cancelled timer {timer_id!r}.")
            return ToolExecutionResult(output=f"Error: timer {timer_id!r} not found")
        return ToolExecutionResult(output=f"Cancelled {count} timer(s).")

    if tool_name == "reschedule_timer":
        timer_id = args.get("id")
        if not isinstance(timer_id, str) or not timer_id.strip():
            raise ValueError("id is required")
        updates = {k: v for k, v in args.items() if k != "id"}
        scheduler.reschedule(timer_id.strip(), updates)
        return ToolExecutionResult(output=f"Rescheduled timer {timer_id.strip()!r}.")

    if tool_name == "list_timers":
        timers = scheduler.list_active()
        return ToolExecutionResult(output=json.dumps(timers, indent=2))

    raise ValueError(f"unknown timer tool {tool_name!r}")
