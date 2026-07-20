"""Tests for companion status bridge (#115)."""

from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from queue import Queue
from unittest.mock import patch

from speech_to_speech.pipeline.messages import LLMResponseChunk

from buddy_tools.companion.bridge import (
    create_and_start_companion_bridge,
    reset_companion_bridge_for_tests,
)
from buddy_tools.companion.config import load_companion_bridge_config
from buddy_tools.companion.events import format_tool_call_summary, salient_pulse_snapshot, tool_call_event
from buddy_tools.companion.publisher import (
    CompanionEventPublisher,
    emit_assistant_text,
    emit_tool_call,
    get_companion_publisher,
    reset_companion_publisher_for_tests,
    set_companion_publisher,
)
from buddy_tools.pulse.state import PulseState, save_pulse_state
from buddy_tools.voice.turn_state import VoiceTurnState, reset_turn_state_for_tests, set_turn_state


class CompanionConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env_backup = {
            "BUDDY_COMPANION_BRIDGE": os.environ.get("BUDDY_COMPANION_BRIDGE"),
            "BUDDY_COMPANION_BRIDGE_HOST": os.environ.get("BUDDY_COMPANION_BRIDGE_HOST"),
            "BUDDY_COMPANION_BRIDGE_PORT": os.environ.get("BUDDY_COMPANION_BRIDGE_PORT"),
        }

    def tearDown(self) -> None:
        for key, value in self._env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_disabled_by_default(self) -> None:
        os.environ.pop("BUDDY_COMPANION_BRIDGE", None)
        self.assertIsNone(load_companion_bridge_config())

    def test_enabled_with_defaults(self) -> None:
        os.environ["BUDDY_COMPANION_BRIDGE"] = "1"
        os.environ.pop("BUDDY_COMPANION_BRIDGE_HOST", None)
        os.environ.pop("BUDDY_COMPANION_BRIDGE_PORT", None)
        config = load_companion_bridge_config()
        self.assertIsNotNone(config)
        assert config is not None
        self.assertEqual(config.host, "127.0.0.1")
        self.assertEqual(config.port, 8766)
        self.assertEqual(config.url, "ws://127.0.0.1:8766")


class CompanionPublisherTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_companion_publisher_for_tests()
        reset_turn_state_for_tests()

    def tearDown(self) -> None:
        reset_companion_publisher_for_tests()
        reset_turn_state_for_tests()

    def test_emit_and_drain_with_no_clients(self) -> None:
        publisher = CompanionEventPublisher(maxsize=4)
        set_companion_publisher(publisher)
        for i in range(10):
            publisher.emit_assistant_text(f"chunk-{i}")
        drained = publisher.drain()
        self.assertEqual(len(drained), 4)
        self.assertEqual(drained[0]["text"], "chunk-6")
        self.assertEqual(drained[-1]["text"], "chunk-9")
        self.assertEqual(publisher.qsize(), 0)

    def test_turn_state_hook_emits_on_transition(self) -> None:
        publisher = CompanionEventPublisher()
        set_companion_publisher(publisher)
        changed = set_turn_state(VoiceTurnState.GENERATING, reason="test")
        self.assertTrue(changed)
        events = publisher.drain()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "turn_state")
        self.assertEqual(events[0]["state"], "generating")
        self.assertEqual(events[0]["reason"], "test")

        # No re-emit when state unchanged
        self.assertFalse(set_turn_state(VoiceTurnState.GENERATING, reason="again"))
        self.assertEqual(publisher.drain(), [])

    def test_emit_assistant_text_no_op_without_publisher(self) -> None:
        reset_companion_publisher_for_tests()
        emit_assistant_text("hello")  # must not raise

    def test_tool_call_event_shape(self) -> None:
        event = tool_call_event(
            tool="list_skills",
            status="ok",
            summary="list_skills · ok",
            source="llm",
            turn_id="turn_1",
            ts="2026-07-20T00:00:00+00:00",
        )
        self.assertEqual(event["type"], "tool_call")
        self.assertEqual(event["tool"], "list_skills")
        self.assertEqual(event["status"], "ok")
        self.assertEqual(event["summary"], "list_skills · ok")
        self.assertEqual(event["source"], "llm")
        self.assertEqual(event["turn_id"], "turn_1")
        self.assertEqual(event["ts"], "2026-07-20T00:00:00+00:00")

    def test_format_tool_call_summary_includes_safe_arg(self) -> None:
        self.assertEqual(
            format_tool_call_summary("list_skills", "ok"),
            "list_skills · ok",
        )
        self.assertEqual(
            format_tool_call_summary("read_memory", "ok", {"scope": "user"}),
            "read_memory · ok · scope=user",
        )

    def test_emit_tool_call_no_op_without_publisher(self) -> None:
        reset_companion_publisher_for_tests()
        emit_tool_call(
            tool="list_skills",
            status="ok",
            summary="list_skills · ok",
        )  # must not raise

    def test_emit_tool_call_with_publisher(self) -> None:
        publisher = CompanionEventPublisher()
        set_companion_publisher(publisher)
        emit_tool_call(
            tool="list_skills",
            status="ok",
            summary="list_skills · ok",
            source="silent",
            turn_id="turn_9",
        )
        events = publisher.drain()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "tool_call")
        self.assertEqual(events[0]["tool"], "list_skills")
        self.assertEqual(events[0]["source"], "silent")
        self.assertEqual(events[0]["turn_id"], "turn_9")
        # Ephemeral — not part of connect snapshots
        self.assertEqual(
            [e["type"] for e in publisher.snapshot_events()],
            [],
        )

    def test_snapshot_caches_latest(self) -> None:
        publisher = CompanionEventPublisher()
        publisher.emit_persona(
            personality_id="coach",
            name="Coach",
            memory_namespace="coach",
            voice_id="ron",
        )
        publisher.emit_turn_state("listening")
        publisher.emit_pulse_state(None)
        snapshots = publisher.snapshot_events()
        self.assertEqual(
            [e["type"] for e in snapshots],
            ["persona", "turn_state", "pulse_state"],
        )
        self.assertEqual(snapshots[0]["name"], "Coach")
        self.assertEqual(snapshots[1]["state"], "listening")
        self.assertFalse(snapshots[2]["active"])

    def test_persona_emit_updates_snapshot(self) -> None:
        publisher = CompanionEventPublisher()
        publisher.emit_persona(
            personality_id="coach",
            name="Coach",
            memory_namespace="coach",
        )
        publisher.emit_persona(
            personality_id="buddy",
            name="Buddy",
            memory_namespace="buddy",
            voice_id="jack",
        )
        snapshots = publisher.snapshot_events()
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0]["id"], "buddy")
        self.assertEqual(snapshots[0]["name"], "Buddy")
        self.assertEqual(snapshots[0]["voice_id"], "jack")


class SalientPulseSnapshotTests(unittest.TestCase):
    def test_inactive_when_none(self) -> None:
        snap = salient_pulse_snapshot(None)
        self.assertEqual(snap["type"], "pulse_state")
        self.assertFalse(snap["active"])
        self.assertNotIn("session_config", snap)

    def test_active_omits_full_session_config(self) -> None:
        state = PulseState(
            skill_name="live-director",
            status="active",
            phase="intro",
            vars={"current_camera": "cam_a"},
            session_config={
                "cameras": {"cam_a": {"label": "Wide", "device": 0}},
                "pulse": {"tick_interval_s": 5},
            },
        )
        snap = salient_pulse_snapshot(state)
        self.assertTrue(snap["active"])
        self.assertEqual(snap["skill_name"], "live-director")
        self.assertEqual(snap["phase"], "intro")
        self.assertEqual(snap["vars"]["current_camera"], "cam_a")
        self.assertEqual(snap["camera_labels"]["cam_a"], "Wide")
        self.assertEqual(
            [row["key"] for row in snap["senses"]],
            ["phase", "pulse_mode", "pending_cue"],
        )
        self.assertNotIn("session_config", snap)
        self.assertNotIn("pulse", snap)

    def test_senses_from_panel_config_list_cameras(self) -> None:
        state = PulseState(
            skill_name="live-director",
            status="active",
            phase="live",
            pulse_mode="directed",
            pending_cue="Switch cameras",
            vars={"current_camera": 2},
            session_config={
                "cameras": [
                    {"id": 1, "label": "wide shot"},
                    {"id": 2, "label": "close-up"},
                ],
                "panel": {
                    "senses": ["phase", "pulse_mode", "current_camera", "pending_cue"],
                },
            },
        )
        snap = salient_pulse_snapshot(state)
        self.assertEqual(snap["camera_labels"]["2"], "close-up")
        by_key = {row["key"]: row for row in snap["senses"]}
        self.assertEqual(by_key["phase"]["value"], "live")
        self.assertEqual(by_key["pulse_mode"]["value"], "directed")
        self.assertEqual(by_key["current_camera"]["label"], "CAMERA")
        self.assertEqual(by_key["current_camera"]["value"], "close-up")
        self.assertEqual(by_key["pending_cue"]["value"], "Switch cameras")
        self.assertNotIn("session_config", snap)


class CompanionBridgeStartTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env_backup = {
            "BUDDY_COMPANION_BRIDGE": os.environ.get("BUDDY_COMPANION_BRIDGE"),
        }
        reset_companion_bridge_for_tests()
        reset_turn_state_for_tests()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.memory_root = Path(self._tmpdir.name) / "memory"
        self.memory_root.mkdir()

    def tearDown(self) -> None:
        for key, value in self._env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        reset_companion_bridge_for_tests()
        reset_turn_state_for_tests()
        self._tmpdir.cleanup()

    def test_create_returns_none_when_disabled(self) -> None:
        os.environ.pop("BUDDY_COMPANION_BRIDGE", None)
        stop = threading.Event()
        bridge = create_and_start_companion_bridge(
            memory_root=self.memory_root,
            persona_namespace="buddy",
            stop_event=stop,
            personality_id="buddy",
            persona_name="Buddy",
        )
        self.assertIsNone(bridge)
        self.assertIsNone(get_companion_publisher())

    def test_create_starts_publisher_and_pulse_watch(self) -> None:
        os.environ["BUDDY_COMPANION_BRIDGE"] = "1"
        stop = threading.Event()
        with patch("buddy_tools.companion.bridge.CompanionBridgeServer.start"):
            bridge = create_and_start_companion_bridge(
                memory_root=self.memory_root,
                persona_namespace="buddy",
                stop_event=stop,
                personality_id="buddy",
                persona_name="Buddy",
                voice_id="jack",
            )
        self.assertIsNotNone(bridge)
        publisher = get_companion_publisher()
        self.assertIsNotNone(publisher)
        assert publisher is not None

        # Seeded persona + turn_state on start
        events = publisher.drain()
        persona_events = [e for e in events if e["type"] == "persona"]
        self.assertTrue(persona_events)
        self.assertEqual(persona_events[0]["id"], "buddy")
        self.assertEqual(persona_events[0]["name"], "Buddy")
        turn_events = [e for e in events if e["type"] == "turn_state"]
        self.assertTrue(turn_events)
        self.assertEqual(turn_events[0]["state"], "listening")

        snapshots = publisher.snapshot_events()
        self.assertEqual(snapshots[0]["type"], "persona")
        self.assertEqual(snapshots[0]["name"], "Buddy")

        save_pulse_state(
            self.memory_root,
            "buddy",
            PulseState(skill_name="live-director", status="active", phase="running"),
        )
        # Wait for pulse watcher poll
        deadline = time.time() + 2.0
        pulse_events: list[dict] = []
        while time.time() < deadline:
            pulse_events.extend(e for e in publisher.drain() if e["type"] == "pulse_state")
            if any(e.get("active") for e in pulse_events):
                break
            time.sleep(0.05)
        stop.set()
        self.assertTrue(any(e.get("active") and e.get("skill_name") == "live-director" for e in pulse_events))


class ExecutorAssistantTextEmitTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_companion_publisher_for_tests()
        self.publisher = CompanionEventPublisher()
        set_companion_publisher(self.publisher)

    def tearDown(self) -> None:
        reset_companion_publisher_for_tests()

    def test_local_tool_executor_emits_assistant_text(self) -> None:
        from speech_to_speech.api.openai_realtime.runtime_config import RuntimeConfig

        from buddy_tools.core.executor import LocalToolExecutor

        stop = threading.Event()
        queue_in: Queue = Queue()
        queue_out: Queue = Queue()
        executor = LocalToolExecutor(
            stop,
            queue_in=queue_in,
            queue_out=queue_out,
            setup_kwargs={},
        )
        chunk = LLMResponseChunk(
            text="Hello ",
            tools=[],
            turn_id="t1",
            turn_revision=1,
            runtime_config=RuntimeConfig(),
        )
        with patch("buddy_tools.core.executor.handle_pulse_response_chunk", return_value=chunk):
            outputs = list(executor.process(chunk))
        self.assertEqual(outputs, [chunk])
        events = self.publisher.drain()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "assistant_text")
        self.assertEqual(events[0]["text"], "Hello ")
        self.assertEqual(events[0]["turn_id"], "t1")


class SchemaDocTests(unittest.TestCase):
    def test_schema_doc_exists(self) -> None:
        path = Path(__file__).resolve().parents[1] / "buddy_tools" / "companion" / "SCHEMA.md"
        self.assertTrue(path.is_file())
        text = path.read_text(encoding="utf-8")
        self.assertIn("turn_state", text)
        self.assertIn("assistant_text", text)
        self.assertIn("speaking_progress", text)
        self.assertIn("pulse_state", text)
        self.assertIn("persona", text)
        self.assertIn("ws://127.0.0.1:8766", text)


if __name__ == "__main__":
    unittest.main()
