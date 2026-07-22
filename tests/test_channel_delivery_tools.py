"""Tests for cross-channel delivery tools (#38)."""

from __future__ import annotations

import unittest
from queue import Queue
from threading import Event
from typing import Any
from unittest.mock import MagicMock

from speech_to_speech.pipeline.messages import EndOfResponse, LLMResponseChunk, TTSInput

from buddy_tools.channels.reply_router import ChannelReplyRouter
from buddy_tools.channels.last_capture import clear_last_capture, store_last_capture
from buddy_tools.channels.telegram import (
    TelegramBridge,
    TelegramConfig,
    resolve_outbound_chat,
    set_telegram_bridge,
)
from buddy_tools.channels.tools import execute_channel_tool
from buddy_tools.channels.turn_context import (
    TurnReplyContext,
    get_turn,
    register_turn,
    reset_turn_contexts,
)
from buddy_tools.core.tool_logging import is_tool_error
from buddy_tools.voice.session import set_tts_handler


class _FakeTtsHandler:
    def __init__(self) -> None:
        self.queue_in: Queue[Any] = Queue()


class ResolveOutboundChatTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_turn_contexts()
        self.stop = Event()
        self.bridge = TelegramBridge(
            TelegramConfig(bot_token="tok", allowed_chat_ids=frozenset({10, 20})),
            runtime_config=MagicMock(),
            text_prompt_queue=Queue(),
            stop_event=self.stop,
        )
        set_telegram_bridge(self.bridge)

    def tearDown(self) -> None:
        set_telegram_bridge(None)
        reset_turn_contexts()

    def test_uses_turn_chat_id(self) -> None:
        register_turn(
            "tg-1",
            TurnReplyContext(channel="telegram", telegram_chat_id=10, telegram_message_thread_id=7),
        )
        chat_id, thread_id = resolve_outbound_chat(turn_id="tg-1")
        self.assertEqual(chat_id, 10)
        self.assertEqual(thread_id, 7)

    def test_uses_last_inbound(self) -> None:
        self.bridge.record_inbound(20, 3)
        chat_id, thread_id = resolve_outbound_chat(turn_id=None)
        self.assertEqual(chat_id, 20)
        self.assertEqual(thread_id, 3)

    def test_uses_sole_allowlist_entry(self) -> None:
        set_telegram_bridge(
            TelegramBridge(
                TelegramConfig(bot_token="tok", allowed_chat_ids=frozenset({42})),
                runtime_config=MagicMock(),
                text_prompt_queue=Queue(),
                stop_event=self.stop,
            )
        )
        chat_id, thread_id = resolve_outbound_chat()
        self.assertEqual(chat_id, 42)
        self.assertIsNone(thread_id)

    def test_errors_when_ambiguous(self) -> None:
        with self.assertRaises(ValueError):
            resolve_outbound_chat()

    def test_explicit_chat_id_must_be_allowlisted(self) -> None:
        with self.assertRaises(ValueError):
            resolve_outbound_chat(chat_id=999)

    def test_errors_without_bridge(self) -> None:
        set_telegram_bridge(None)
        with self.assertRaises(ValueError):
            resolve_outbound_chat(chat_id=10)


class SendTelegramMessageToolTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_turn_contexts()
        self.stop = Event()
        self.sent: list[tuple[int, str, int | None]] = []
        self.bridge = TelegramBridge(
            TelegramConfig(bot_token="tok", allowed_chat_ids=frozenset({55})),
            runtime_config=MagicMock(),
            text_prompt_queue=Queue(),
            stop_event=self.stop,
        )
        self.bridge.send_reply = self._send  # type: ignore[method-assign]
        set_telegram_bridge(self.bridge)

    def tearDown(self) -> None:
        set_telegram_bridge(None)
        reset_turn_contexts()

    def _send(self, chat_id: int, text: str, message_thread_id: int | None = None) -> None:
        self.sent.append((chat_id, text, message_thread_id))

    def test_sends_via_sole_allowlist_and_voice_turn(self) -> None:
        result = execute_channel_tool(
            "send_telegram_message",
            {"text": "Summary for phone"},
            turn_id="voice-1",
        )
        self.assertFalse(is_tool_error(result))
        self.assertEqual(self.sent, [(55, "Summary for phone", None)])

    def test_uses_turn_chat_id_and_suppresses_default_router(self) -> None:
        register_turn(
            "tg-42",
            TurnReplyContext(channel="telegram", telegram_chat_id=55),
        )
        result = execute_channel_tool(
            "send_telegram_message",
            {"text": "Tool-sent body"},
            turn_id="tg-42",
        )
        self.assertFalse(is_tool_error(result))
        self.assertEqual(self.sent, [(55, "Tool-sent body", None)])

        ctx = get_turn("tg-42")
        self.assertIsNotNone(ctx)
        assert ctx is not None
        self.assertTrue(ctx.suppress_default_telegram_reply)

        router = ChannelReplyRouter(Event(), Queue(), Queue())
        router_sent: list[tuple[int, str, int | None]] = []
        router.setup(send_telegram_reply=lambda c, t, th: router_sent.append((c, t, th)))
        list(router.process(LLMResponseChunk(
            text="LLM also said this",
            turn_id="tg-42",
            turn_revision=0,
        )))
        list(router.process(EndOfResponse(turn_id="tg-42", turn_revision=0)))
        self.assertEqual(router_sent, [])

    def test_errors_when_bridge_missing(self) -> None:
        set_telegram_bridge(None)
        result = execute_channel_tool("send_telegram_message", {"text": "hi"})
        self.assertTrue(is_tool_error(result))

    def test_errors_on_empty_text(self) -> None:
        result = execute_channel_tool("send_telegram_message", {"text": "  "})
        self.assertTrue(is_tool_error(result))


class SendTelegramPhotoToolTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_turn_contexts()
        clear_last_capture()
        self.stop = Event()
        self.sent: list[dict[str, Any]] = []
        self.bridge = TelegramBridge(
            TelegramConfig(bot_token="tok", allowed_chat_ids=frozenset({55})),
            runtime_config=MagicMock(),
            text_prompt_queue=Queue(),
            stop_event=self.stop,
        )
        self.bridge.send_document = self._send_document  # type: ignore[method-assign]
        set_telegram_bridge(self.bridge)

        import cv2
        import numpy as np

        from buddy_tools.channels.images import bytes_to_jpeg_data_uri

        frame = np.zeros((32, 32, 3), dtype=np.uint8)
        ok, jpeg = cv2.imencode(".jpg", frame)
        assert ok
        self.delivery_jpeg = jpeg.tobytes()
        self.data_uri = bytes_to_jpeg_data_uri(self.delivery_jpeg, max_width=32)
        store_last_capture(
            self.data_uri,
            delivery_jpeg=self.delivery_jpeg,
            filename="buddy-camera.jpg",
        )

    def tearDown(self) -> None:
        set_telegram_bridge(None)
        clear_last_capture()
        reset_turn_contexts()

    def _send_document(
        self,
        chat_id: int,
        file_bytes: bytes,
        *,
        filename: str = "capture.jpg",
        caption: str | None = None,
        message_thread_id: int | None = None,
    ) -> None:
        self.sent.append(
            {
                "chat_id": chat_id,
                "file_bytes": file_bytes,
                "filename": filename,
                "caption": caption,
                "message_thread_id": message_thread_id,
            }
        )

    def test_sends_last_capture_with_caption(self) -> None:
        result = execute_channel_tool(
            "send_telegram_photo",
            {"caption": "Here is your screen"},
            turn_id="voice-1",
        )
        self.assertFalse(is_tool_error(result))
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0]["chat_id"], 55)
        self.assertEqual(self.sent[0]["caption"], "Here is your screen")
        self.assertEqual(self.sent[0]["file_bytes"], self.delivery_jpeg)
        self.assertEqual(self.sent[0]["filename"], "buddy-camera.jpg")

    def test_legacy_uri_only_store_still_delivers(self) -> None:
        clear_last_capture()
        store_last_capture(self.data_uri)
        result = execute_channel_tool(
            "send_telegram_photo",
            {"caption": "legacy"},
            turn_id="voice-1",
        )
        self.assertFalse(is_tool_error(result))
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(self.sent[0]["file_bytes"])
        self.assertEqual(self.sent[0]["filename"], "buddy-capture.jpg")

    def test_errors_without_last_capture(self) -> None:
        clear_last_capture()
        result = execute_channel_tool("send_telegram_photo", {})
        self.assertTrue(is_tool_error(result))
        self.assertIn("capture", result.output.lower())

    def test_errors_when_bridge_missing(self) -> None:
        set_telegram_bridge(None)
        result = execute_channel_tool("send_telegram_photo", {"caption": "x"})
        self.assertTrue(is_tool_error(result))

    def test_telegram_turn_suppresses_default_router(self) -> None:
        register_turn(
            "tg-photo",
            TurnReplyContext(channel="telegram", telegram_chat_id=55),
        )
        result = execute_channel_tool(
            "send_telegram_photo",
            {"caption": "photo"},
            turn_id="tg-photo",
        )
        self.assertFalse(is_tool_error(result))
        ctx = get_turn("tg-photo")
        self.assertIsNotNone(ctx)
        assert ctx is not None
        self.assertTrue(ctx.suppress_default_telegram_reply)


class SpeakAloudToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.handler = _FakeTtsHandler()
        set_tts_handler(self.handler)

    def tearDown(self) -> None:
        set_tts_handler(None)

    def test_enqueues_tts_input_and_end_of_response(self) -> None:
        result = execute_channel_tool(
            "speak_aloud",
            {"text": "Hello from the phone"},
            turn_id="tg-speak",
            turn_revision=2,
        )
        self.assertFalse(is_tool_error(result))
        first = self.handler.queue_in.get_nowait()
        second = self.handler.queue_in.get_nowait()
        self.assertIsInstance(first, TTSInput)
        assert isinstance(first, TTSInput)
        self.assertEqual(first.text, "Hello from the phone")
        self.assertEqual(first.turn_id, "tg-speak")
        self.assertEqual(first.turn_revision, 2)
        self.assertIsInstance(second, EndOfResponse)
        assert isinstance(second, EndOfResponse)
        self.assertEqual(second.turn_id, "tg-speak")
        self.assertEqual(second.turn_revision, 2)

    def test_errors_without_tts_handler(self) -> None:
        set_tts_handler(None)
        result = execute_channel_tool("speak_aloud", {"text": "hi"})
        self.assertTrue(is_tool_error(result))

    def test_errors_on_empty_text(self) -> None:
        result = execute_channel_tool("speak_aloud", {"text": ""})
        self.assertTrue(is_tool_error(result))


if __name__ == "__main__":
    unittest.main()
