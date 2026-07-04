"""Tests for timer tools (#46)."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from queue import Empty, Queue
from threading import Event
from unittest.mock import Mock

from speech_to_speech.api.openai_realtime.runtime_config import RuntimeConfig
from speech_to_speech.pipeline.messages import GenerateResponseRequest

from buddy_tools.bootstrap import set_memory_root
from buddy_tools.listening_pause import get_listening_pause_controller
from buddy_tools.registry import ALL_TOOL_DEFINITIONS, execute_tool
from buddy_tools.skills import _cancel_skill, save_skill_state, SkillState
from buddy_tools.timers import (
    TIMER_NUDGE_PREFIX,
    TimerConfig,
    configure_timers,
    execute_timer_tool,
    get_timer_scheduler,
    notify_user_speech,
    reset_timer_scheduler_for_tests,
    set_perf_counter_for_tests,
)


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, delta: float) -> None:
        self.t += delta


class TimerToolTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_timer_scheduler_for_tests()
        get_listening_pause_controller().paused = False
        self.clock = FakeClock()
        set_perf_counter_for_tests(self.clock)
        self.queue: Queue = Queue()
        self.should_listen = Event()
        self.should_listen.set()
        self.runtime_config = RuntimeConfig()
        self.runtime_config.chat.add_item = Mock()
        configure_timers(
            text_prompt_queue=self.queue,
            runtime_config=self.runtime_config,
            should_listen=self.should_listen,
        )

    def tearDown(self) -> None:
        reset_timer_scheduler_for_tests()
        get_listening_pause_controller().paused = False

    def test_timer_tools_registered(self) -> None:
        names = {tool.name for tool in ALL_TOOL_DEFINITIONS}
        self.assertIn("start_timer", names)
        self.assertIn("cancel_timer", names)
        self.assertIn("list_timers", names)
        self.assertIn("reschedule_timer", names)

    def test_start_cancel_list(self) -> None:
        result = execute_timer_tool(
            "start_timer",
            {
                "id": "check-in",
                "prompt": "Say hello",
                "mode": "repeat",
                "gate": "wall_clock",
                "interval_seconds": 30,
            },
        )
        self.assertIn("Started timer", result.output)

        listed = json.loads(execute_timer_tool("list_timers", {}).output)
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["id"], "check-in")
        self.assertEqual(listed[0]["mode"], "repeat")
        self.assertIn("seconds_until_next", listed[0])

        cancel_result = execute_timer_tool("cancel_timer", {"id": "check-in"})
        self.assertIn("Cancelled timer", cancel_result.output)
        self.assertEqual(json.loads(execute_timer_tool("list_timers", {}).output), [])

    def test_duplicate_id_rejected_without_replace(self) -> None:
        args = {
            "id": "dup",
            "prompt": "one",
            "mode": "once",
            "gate": "wall_clock",
            "delay_seconds": 5,
        }
        execute_timer_tool("start_timer", args)
        with self.assertRaises(ValueError):
            execute_timer_tool("start_timer", args)

    def test_replace_updates_timer(self) -> None:
        execute_timer_tool(
            "start_timer",
            {
                "id": "pace",
                "prompt": "slow",
                "mode": "repeat",
                "gate": "wall_clock",
                "interval_seconds": 60,
            },
        )
        execute_timer_tool(
            "start_timer",
            {
                "id": "pace",
                "prompt": "fast",
                "mode": "repeat",
                "gate": "wall_clock",
                "interval_seconds": 10,
                "replace": True,
            },
        )
        listed = json.loads(execute_timer_tool("list_timers", {}).output)
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["interval_seconds"], 10)

    def test_reschedule_updates_timing(self) -> None:
        execute_timer_tool(
            "start_timer",
            {
                "id": "work",
                "prompt": "work interval",
                "mode": "repeat",
                "gate": "wall_clock",
                "interval_seconds": 40,
            },
        )
        execute_timer_tool(
            "reschedule_timer",
            {"id": "work", "interval_seconds": 20},
        )
        listed = json.loads(execute_timer_tool("list_timers", {}).output)
        self.assertEqual(listed[0]["interval_seconds"], 20)

    def test_inject_on_wake(self) -> None:
        scheduler = get_timer_scheduler()
        scheduler.start(
            TimerConfig(
                id="fire",
                prompt="Proactive nudge",
                mode="once",
                gate="wall_clock",
                delay_seconds=0.05,
                defer_while_busy=False,
            )
        )
        active = scheduler._timers["fire"]
        scheduler._on_wake("fire", active.generation)

        self.runtime_config.chat.add_item.assert_called_once()
        nudge_call = self.runtime_config.chat.add_item.call_args[0][0]
        self.assertEqual(nudge_call.role, "user")
        self.assertIn("Proactive nudge", nudge_call.content[0].text)
        self.assertIn(TIMER_NUDGE_PREFIX, nudge_call.content[0].text)
        req = self.queue.get_nowait()
        self.assertIsInstance(req, GenerateResponseRequest)
        self.assertIsNone(req.turn_id)
        self.assertIsNone(req.turn_revision)
        assert req.response is not None
        self.assertIn("Proactive nudge", req.response.instructions or "")
        self.assertEqual(json.loads(execute_timer_tool("list_timers", {}).output), [])

    def test_defer_while_busy(self) -> None:
        scheduler = get_timer_scheduler()
        scheduler.start(
            TimerConfig(
                id="busy",
                prompt="Later",
                mode="once",
                gate="wall_clock",
                delay_seconds=0.01,
                defer_while_busy=True,
            )
        )
        self.should_listen.clear()
        active = scheduler._timers["busy"]
        generation = active.generation
        scheduler._on_wake("busy", generation)

        with self.assertRaises(Empty):
            self.queue.get_nowait()
        self.assertIn("busy", scheduler._timers)

        self.should_listen.set()
        new_active = scheduler._timers["busy"]
        scheduler._on_wake("busy", new_active.generation)
        self.assertIsInstance(self.queue.get_nowait(), GenerateResponseRequest)

    def test_listening_pause_blocks_inject(self) -> None:
        controller = get_listening_pause_controller()
        controller.paused = True
        scheduler = get_timer_scheduler()
        scheduler.start(
            TimerConfig(
                id="paused",
                prompt="Should not fire",
                mode="once",
                gate="wall_clock",
                delay_seconds=0.01,
                defer_while_busy=False,
            )
        )
        active = scheduler._timers["paused"]
        scheduler._on_wake("paused", active.generation)

        with self.assertRaises(Empty):
            self.queue.get_nowait()
        self.runtime_config.chat.add_item.assert_not_called()
        self.assertIn("paused", scheduler._timers)

    def test_after_silence_gate_defers_until_silent(self) -> None:
        notify_user_speech(1000.0)
        self.clock.advance(1.0)
        scheduler = get_timer_scheduler()
        scheduler.start(
            TimerConfig(
                id="silence",
                prompt="Check in",
                mode="once",
                gate="after_silence",
                delay_seconds=5.0,
                defer_while_busy=False,
            )
        )
        active = scheduler._timers["silence"]
        scheduler._on_wake("silence", active.generation)

        with self.assertRaises(Empty):
            self.queue.get_nowait()

        self.clock.advance(5.0)
        active = scheduler._timers["silence"]
        scheduler._on_wake("silence", active.generation)
        self.assertIsInstance(self.queue.get_nowait(), GenerateResponseRequest)

    def test_cancel_on_user_speech(self) -> None:
        execute_timer_tool(
            "start_timer",
            {
                "id": "hangout",
                "prompt": "nudge",
                "mode": "once",
                "gate": "after_silence",
                "delay_seconds": 30,
                "cancel_on_user_speech": True,
            },
        )
        notify_user_speech(2000.0)
        self.assertEqual(json.loads(execute_timer_tool("list_timers", {}).output), [])

    def test_cancel_all_timers(self) -> None:
        execute_timer_tool(
            "start_timer",
            {
                "id": "a",
                "prompt": "a",
                "mode": "once",
                "gate": "wall_clock",
                "delay_seconds": 10,
            },
        )
        execute_timer_tool(
            "start_timer",
            {
                "id": "b",
                "prompt": "b",
                "mode": "once",
                "gate": "wall_clock",
                "delay_seconds": 10,
            },
        )
        result = execute_timer_tool("cancel_timer", {})
        self.assertIn("Cancelled 2 timer(s)", result.output)

    def test_real_timer_fires_after_delay(self) -> None:
        set_perf_counter_for_tests(None)
        execute_timer_tool(
            "start_timer",
            {
                "id": "real",
                "prompt": "real tick",
                "mode": "once",
                "gate": "wall_clock",
                "delay_seconds": 0.08,
                "defer_while_busy": False,
            },
        )
        time.sleep(0.2)
        self.assertIsInstance(self.queue.get_nowait(), GenerateResponseRequest)

    def test_execute_tool_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            set_memory_root(Path(tmp))
            result = execute_tool(
                Path(tmp),
                "start_timer",
                json.dumps(
                    {
                        "id": "via-registry",
                        "prompt": "hi",
                        "mode": "once",
                        "gate": "wall_clock",
                        "delay_seconds": 60,
                    }
                ),
                persona_namespace="buddy",
            )
            self.assertIn("Started timer", result.output)
            listed = json.loads(
                execute_tool(Path(tmp), "list_timers", "{}", persona_namespace="buddy").output
            )
            self.assertEqual(len(listed), 1)

    def test_cancel_skill_clears_skill_timers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            set_memory_root(Path(tmp))
            save_skill_state(
                Path(tmp),
                "buddy",
            SkillState(
                skill_name="workout",
                status="in_progress",
                step_index=0,
                skill_type="generic",
            ),
            )
            execute_timer_tool(
                "start_timer",
                {
                    "id": "interval",
                    "prompt": "next set",
                    "mode": "repeat",
                    "gate": "wall_clock",
                    "interval_seconds": 30,
                    "skill_name": "workout",
                },
            )
            _cancel_skill(Path(tmp), "buddy")
            self.assertEqual(json.loads(execute_timer_tool("list_timers", {}).output), [])


class TimerSessionCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_timer_scheduler_for_tests()

    def tearDown(self) -> None:
        reset_timer_scheduler_for_tests()

    def test_on_session_end_clears_timers(self) -> None:
        from buddy_tools.executor import LocalToolExecutor

        configure_timers(
            text_prompt_queue=Queue(),
            runtime_config=RuntimeConfig(),
            should_listen=Event(),
        )
        execute_timer_tool(
            "start_timer",
            {
                "id": "session",
                "prompt": "x",
                "mode": "once",
                "gate": "wall_clock",
                "delay_seconds": 60,
            },
        )
        executor = LocalToolExecutor(Event(), queue_in=Queue(), queue_out=Queue())
        executor.on_session_end()
        self.assertEqual(json.loads(execute_timer_tool("list_timers", {}).output), [])


if __name__ == "__main__":
    unittest.main()
