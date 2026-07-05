"""Tests for episodic memory Phase 2 — turn logging and persona boundaries."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from queue import Queue
from threading import Event
from unittest.mock import Mock, patch

from openai.types.responses.response_function_tool_call import ResponseFunctionToolCall

from buddy_tools.channels.telegram import (
    enqueue_telegram_photo_turn,
    enqueue_telegram_text_turn,
    text_only_response_params,
)
from buddy_tools.channels.turn_context import reset_turn_contexts
from buddy_tools.core.executor import LocalToolExecutor
from buddy_tools.core.result import ToolExecutionResult
from buddy_tools.episodic import (
    EpisodicTurnRecord,
    configure_episodic,
    get_episodic_manager,
    reconfigure_episodic_persona,
    reset_episodic_for_tests,
)
from buddy_tools.episodic.config import EpisodicConfig
from buddy_tools.episodic.manager import EpisodicSessionManager
from buddy_tools.episodic.paths import TURNS_FILENAME, episodic_root
from buddy_tools.episodic.session import EpisodicSession, load_session
from buddy_tools.episodic.turns import append_turn, load_turns
from buddy_tools.voice.listening_pause import (
    ListeningPauseController,
    process_transcription_with_listening_pause,
)
from speech_to_speech.api.openai_realtime.runtime_config import RuntimeConfig
from speech_to_speech.LLM.chat import Chat
from speech_to_speech.pipeline.messages import (
    EndOfResponse,
    GenerateResponseRequest,
    LLMResponseChunk,
    Transcription,
)
from speech_to_speech.pipeline.speculative_turns import SpeculativeTurnTracker


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self._current = start

    def now(self) -> datetime:
        return self._current

    def advance(self, *, seconds: float = 0, minutes: float = 0) -> None:
        delta = timedelta(seconds=seconds, minutes=minutes)
        self._current = self._current + delta


def _make_manager(
    memory_root,
    persona: str = "buddy",
    *,
    clock: FakeClock,
    idle_minutes: int = 20,
) -> EpisodicSessionManager:
    config = EpisodicConfig(
        idle_timeout_minutes=idle_minutes,
        max_session_minutes=120,
        timezone="America/New_York",
    )
    return EpisodicSessionManager(
        memory_root,
        persona,
        config=config,
        now_fn=clock.now,
    )


def _find_turns_path(memory_root, persona: str = "buddy"):
    for path in episodic_root(memory_root, persona).rglob(TURNS_FILENAME):
        return path
    raise AssertionError("turns.jsonl not found")


class EpisodicTurnSchemaTests(unittest.TestCase):
    def test_append_and_load_turns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp) / "session"
            session_dir.mkdir()
            session = EpisodicSession(
                session_id="20260705T120000-abc12345",
                status="open",
                started_at="2026-07-05T16:00:00+00:00",
                persona_namespace="buddy",
            )
            record = EpisodicTurnRecord(
                seq=1,
                role="user",
                channel="voice",
                turn_id="voice-1",
                text="Hello",
            )
            append_turn(session_dir, session, record)
            turns = load_turns(session_dir / TURNS_FILENAME)
            self.assertEqual(len(turns), 1)
            self.assertEqual(turns[0]["role"], "user")
            self.assertEqual(turns[0]["text"], "Hello")
            self.assertEqual(session.turn_count, 1)


class VoiceTurnLoggingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.memory_root = Path(self._tmpdir.name) / "memory"
        self.memory_root.mkdir()
        self.clock = FakeClock(datetime(2026, 7, 5, 15, 0, 0, tzinfo=UTC))
        reset_episodic_for_tests()
        configure_episodic(
            self.memory_root,
            "buddy",
            config=EpisodicConfig(20, 120, "America/New_York"),
        )
        manager = get_episodic_manager()
        assert manager is not None
        manager._now_fn = self.clock.now

        self.controller = ListeningPauseController(should_listen=Event())
        self.notifier = Mock()
        self.notifier.text_output_queue = None
        self.notifier.should_listen = Event()
        self.notifier.runtime_config = RuntimeConfig()
        self.notifier.runtime_config.chat.add_item = Mock()

    def tearDown(self) -> None:
        reset_episodic_for_tests()
        reset_turn_contexts()
        self._tmpdir.cleanup()

    def test_committed_transcription_logs_user_turn(self) -> None:
        transcription = Transcription(
            text="What is the weather?",
            language_code="en",
            turn_id="voice-42",
            turn_revision=0,
        )
        list(
            process_transcription_with_listening_pause(
                self.notifier,
                transcription,
                controller=self.controller,
            )
        )

        turns_path = _find_turns_path(self.memory_root)
        turns = load_turns(turns_path)
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]["role"], "user")
        self.assertEqual(turns[0]["channel"], "voice")
        self.assertEqual(turns[0]["turn_id"], "voice-42")
        self.assertEqual(turns[0]["text"], "What is the weather?")

        manager = get_episodic_manager()
        assert manager is not None
        session = manager.current_session()
        assert session is not None
        self.assertEqual(session.turn_count, 1)
        self.assertIn("voice", session.channels)


class TelegramTurnLoggingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.memory_root = Path(self._tmpdir.name) / "memory"
        self.memory_root.mkdir()
        reset_episodic_for_tests()
        configure_episodic(self.memory_root, "buddy")
        self.runtime_config = RuntimeConfig()
        self.runtime_config.chat = Chat(10)
        self.queue: Queue = Queue()

    def tearDown(self) -> None:
        reset_episodic_for_tests()
        reset_turn_contexts()
        self._tmpdir.cleanup()

    def test_text_turn_logged(self) -> None:
        enqueue_telegram_text_turn(
            runtime_config=self.runtime_config,
            text_prompt_queue=self.queue,
            turn_id="tg-100",
            chat_id=12345,
            text="Hello from Telegram",
        )
        turns = load_turns(_find_turns_path(self.memory_root))
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]["role"], "user")
        self.assertEqual(turns[0]["channel"], "telegram")
        self.assertEqual(turns[0]["turn_id"], "tg-100")
        self.assertEqual(turns[0]["text"], "Hello from Telegram")

    def test_photo_turn_logged_without_image_data(self) -> None:
        enqueue_telegram_photo_turn(
            runtime_config=self.runtime_config,
            text_prompt_queue=self.queue,
            turn_id="tg-101",
            chat_id=12345,
            image_data_uri="data:image/jpeg;base64,SECRET",
            caption="My photo",
        )
        turns = load_turns(_find_turns_path(self.memory_root))
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]["content_type"], "photo")
        self.assertTrue(turns[0]["has_image"])
        self.assertEqual(turns[0]["text"], "My photo")
        self.assertNotIn("SECRET", json.dumps(turns))


class CrossChannelSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.memory_root = Path(self._tmpdir.name) / "memory"
        self.memory_root.mkdir()
        reset_episodic_for_tests()
        configure_episodic(self.memory_root, "buddy")

    def tearDown(self) -> None:
        reset_episodic_for_tests()
        reset_turn_contexts()
        self._tmpdir.cleanup()

    def test_voice_then_telegram_shares_one_session(self) -> None:
        manager = get_episodic_manager()
        assert manager is not None
        manager.on_user_activity("voice")
        session_id = manager.current_session().session_id  # type: ignore[union-attr]
        manager.log_turn(
            EpisodicTurnRecord(role="user", channel="voice", turn_id="v1", text="Hi voice")
        )

        runtime_config = RuntimeConfig()
        runtime_config.chat = Chat(10)
        enqueue_telegram_text_turn(
            runtime_config=runtime_config,
            text_prompt_queue=Queue(),
            turn_id="tg-200",
            chat_id=1,
            text="Hi telegram",
        )

        self.assertEqual(manager.current_session().session_id, session_id)  # type: ignore[union-attr]
        session = manager.current_session()
        assert session is not None
        self.assertEqual(sorted(session.channels), ["telegram", "voice"])

        turns = load_turns(_find_turns_path(self.memory_root))
        self.assertEqual(len(turns), 2)
        self.assertEqual(turns[0]["channel"], "voice")
        self.assertEqual(turns[1]["channel"], "telegram")


class ExecutorTurnLoggingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.memory_root = Path(self._tmpdir.name) / "memory"
        self.memory_root.mkdir()
        reset_episodic_for_tests()
        configure_episodic(self.memory_root, "buddy")
        reset_turn_contexts()

        self.chat = Chat(10)
        self.runtime_config = RuntimeConfig()
        self.runtime_config.chat = self.chat
        self.executor = LocalToolExecutor(Event(), Queue(), Queue())
        self.executor.setup(
            text_prompt_queue=Queue(),
            memory_root=self.memory_root,
            persona_namespace="buddy",
        )

    def tearDown(self) -> None:
        reset_episodic_for_tests()
        reset_turn_contexts()
        self._tmpdir.cleanup()

    def _pending_context(self, turn_id: str = "turn-1") -> GenerateResponseRequest:
        return GenerateResponseRequest(
            runtime_config=self.runtime_config,
            response=text_only_response_params(),
            language_code="en",
            turn_id=turn_id,
            turn_revision=0,
            speech_stopped_at_s=0.0,
        )

    def test_tool_call_logged_with_safe_args(self) -> None:
        manager = get_episodic_manager()
        assert manager is not None
        manager.on_user_activity("voice")

        self.executor._pending_context = self._pending_context()
        self.executor._pending_tools = [
            ResponseFunctionToolCall(
                type="function_call",
                name="list_skills",
                arguments='{"include_body": true}',
                call_id="call_1",
                id="fc_1",
            )
        ]

        with patch(
            "buddy_tools.core.executor.execute_tool",
            return_value=ToolExecutionResult(output='[{"name":"demo"}]'),
        ):
            self.assertTrue(self.executor._execute_pending_tools())

        turns = load_turns(_find_turns_path(self.memory_root))
        tool_turns = [t for t in turns if t["role"] == "tool"]
        self.assertEqual(len(tool_turns), 1)
        self.assertEqual(tool_turns[0]["tool_name"], "list_skills")
        self.assertTrue(tool_turns[0]["tool_success"])
        self.assertIn("tool_args", tool_turns[0])

    def test_assistant_turn_logged_on_end_of_response(self) -> None:
        manager = get_episodic_manager()
        assert manager is not None
        manager.on_user_activity("voice")

        chunk = LLMResponseChunk(
            runtime_config=self.runtime_config,
            response=text_only_response_params(),
            text="Hello ",
            turn_id="turn-99",
            turn_revision=0,
        )
        end = EndOfResponse(turn_id="turn-99", turn_revision=0)

        list(self.executor.process(chunk))
        list(self.executor.process(LLMResponseChunk(
            runtime_config=self.runtime_config,
            response=text_only_response_params(),
            text="world",
            turn_id="turn-99",
            turn_revision=0,
        )))
        list(self.executor.process(end))

        turns = load_turns(_find_turns_path(self.memory_root))
        assistant = [t for t in turns if t["role"] == "assistant"]
        self.assertEqual(len(assistant), 1)
        self.assertEqual(assistant[0]["text"], "Hello world")
        self.assertEqual(assistant[0]["turn_id"], "turn-99")

    def test_speculative_cancel_skips_assistant_log(self) -> None:
        manager = get_episodic_manager()
        assert manager is not None
        manager.on_user_activity("voice")

        speculative = Mock(spec=SpeculativeTurnTracker)
        speculative.is_latest_after_reopen_grace.return_value = False
        self.executor.speculative_turns = speculative

        chunk = LLMResponseChunk(
            runtime_config=self.runtime_config,
            response=text_only_response_params(),
            text="Cancelled reply",
            turn_id="turn-cancel",
            turn_revision=1,
        )
        end = EndOfResponse(turn_id="turn-cancel", turn_revision=1)

        list(self.executor.process(chunk))
        list(self.executor.process(end))

        turns = load_turns(_find_turns_path(self.memory_root))
        self.assertEqual([t for t in turns if t["role"] == "assistant"], [])


class PersonalitySwitchSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.memory_root = Path(self._tmpdir.name) / "memory"
        self.memory_root.mkdir()
        reset_episodic_for_tests()
        configure_episodic(self.memory_root, "buddy")

        self.chat = Chat(10)
        self.runtime_config = RuntimeConfig()
        self.runtime_config.chat = self.chat
        self.executor = LocalToolExecutor(Event(), Queue(), Queue())
        self.executor.setup(
            text_prompt_queue=Queue(),
            memory_root=self.memory_root,
            persona_namespace="buddy",
        )

    def tearDown(self) -> None:
        reset_episodic_for_tests()
        self._tmpdir.cleanup()

    def test_personality_switch_closes_session_and_splits_namespace(self) -> None:
        manager = get_episodic_manager()
        assert manager is not None
        manager.on_user_activity("voice")
        manager.log_turn(
            EpisodicTurnRecord(role="user", channel="voice", turn_id="v1", text="Switch me")
        )
        old_session_id = manager.current_session().session_id  # type: ignore[union-attr]

        self.executor._pending_context = GenerateResponseRequest(
            runtime_config=self.runtime_config,
            response=text_only_response_params(),
            turn_id="v1",
            turn_revision=0,
        )
        self.executor._pending_tools = [
            ResponseFunctionToolCall(
                type="function_call",
                name="switch_personality",
                arguments='{"personality_id": "coach"}',
                call_id="call_sw",
                id="fc_sw",
            )
        ]

        coach_profile = Mock()
        coach_profile.memory_namespace = "coach"
        coach_profile.name = "Coach"
        coach_profile.id = "coach"

        with patch(
            "buddy_tools.core.executor.execute_tool",
            return_value=ToolExecutionResult(output="ok", personality_switch_id="coach"),
        ), patch(
            "buddy_tools.core.executor.apply_personality_switch",
            return_value=coach_profile,
        ):
            self.assertTrue(self.executor._execute_pending_tools())

        old_path = None
        for path in episodic_root(self.memory_root, "buddy").rglob("session.json"):
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("session_id") == old_session_id:
                old_path = path
                break
        assert old_path is not None
        old_session = load_session(old_path)
        assert old_session is not None
        self.assertEqual(old_session.status, "close_pending")
        self.assertEqual(old_session.idle_reason, "personality_switch")

        new_manager = get_episodic_manager()
        assert new_manager is not None
        self.assertEqual(new_manager.persona_namespace, "coach")
        self.assertIsNone(new_manager.current_session())

        new_manager.on_user_activity("voice")
        self.assertIsNotNone(new_manager.current_session())
        self.assertNotEqual(new_manager.current_session().session_id, old_session_id)  # type: ignore[union-attr]


class ShutdownForceCloseTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.memory_root = Path(self._tmpdir.name) / "memory"
        self.memory_root.mkdir()
        reset_episodic_for_tests()
        configure_episodic(self.memory_root, "buddy")

        self.executor = LocalToolExecutor(Event(), Queue(), Queue())
        self.executor.setup(memory_root=self.memory_root, persona_namespace="buddy")

    def tearDown(self) -> None:
        reset_episodic_for_tests()
        self._tmpdir.cleanup()

    def test_on_session_end_closes_with_replayable_turns(self) -> None:
        manager = get_episodic_manager()
        assert manager is not None
        manager.on_user_activity("voice")
        manager.log_turn(
            EpisodicTurnRecord(role="user", channel="voice", turn_id="v1", text="Before shutdown")
        )
        session_id = manager.current_session().session_id  # type: ignore[union-attr]
        turns_path = _find_turns_path(self.memory_root)

        self.executor.on_session_end()

        self.assertIsNone(get_episodic_manager().current_session())  # type: ignore[union-attr]

        session_path = None
        for path in episodic_root(self.memory_root, "buddy").rglob("session.json"):
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("session_id") == session_id:
                session_path = path
                break
        assert session_path is not None
        closed = load_session(session_path)
        assert closed is not None
        self.assertEqual(closed.status, "close_pending")
        self.assertEqual(closed.idle_reason, "shutdown")

        replay = load_turns(turns_path)
        self.assertEqual(len(replay), 1)
        self.assertEqual(replay[0]["text"], "Before shutdown")


class ReconfigurePersonaTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.memory_root = Path(self._tmpdir.name) / "memory"
        self.memory_root.mkdir()
        reset_episodic_for_tests()
        configure_episodic(self.memory_root, "buddy")

    def tearDown(self) -> None:
        reset_episodic_for_tests()
        self._tmpdir.cleanup()

    def test_reconfigure_switches_namespace(self) -> None:
        reconfigure_episodic_persona("coach")
        manager = get_episodic_manager()
        assert manager is not None
        self.assertEqual(manager.persona_namespace, "coach")


if __name__ == "__main__":
    unittest.main()
