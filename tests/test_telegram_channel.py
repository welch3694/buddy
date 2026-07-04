"""Tests for Telegram channel integration (#34)."""

from __future__ import annotations

import base64
import json
import os
import tempfile
import unittest
from queue import Queue
from threading import Event

import cv2
import numpy as np
from speech_to_speech.api.openai_realtime.runtime_config import RuntimeConfig
from speech_to_speech.pipeline.messages import EndOfResponse, GenerateResponseRequest, LLMResponseChunk

from buddy_tools.channels.images import DEFAULT_MAX_WIDTH, bytes_to_jpeg_data_uri
from buddy_tools.channels.reply_router import ChannelReplyRouter
from buddy_tools.channels.telegram import (
    enqueue_telegram_photo_turn,
    enqueue_telegram_text_turn,
    is_chat_allowed,
    load_allowed_chat_ids_from_file,
    load_telegram_config,
    parse_allowed_chat_ids,
    text_only_response_params,
)
from buddy_tools.channels.turn_context import TurnReplyContext, get_turn, register_turn, reset_turn_contexts
from buddy_tools.data_dir import reset_data_dir_config
from buddy_tools.executor import LocalToolExecutor


def _make_jpeg_bytes(width: int = 1024, height: int = 768) -> bytes:
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    frame[:, :, 2] = 255
    ok, jpeg = cv2.imencode(".jpg", frame)
    assert ok
    return jpeg.tobytes()


class ParseAllowedChatIdsTests(unittest.TestCase):
    def test_parse_comma_separated_ids(self) -> None:
        self.assertEqual(parse_allowed_chat_ids("123, 456 ,789"), frozenset({123, 456, 789}))

    def test_is_chat_allowed(self) -> None:
        allowed = frozenset({111, 222})
        self.assertTrue(is_chat_allowed(111, allowed))
        self.assertFalse(is_chat_allowed(999, allowed))


class LoadTelegramConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env_backup = {
            "TELEGRAM_BOT_TOKEN": os.environ.get("TELEGRAM_BOT_TOKEN"),
            "TELEGRAM_ALLOWED_CHAT_IDS": os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS"),
        }

    def tearDown(self) -> None:
        for key, value in self._env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        reset_data_dir_config()

    def test_returns_none_without_token(self) -> None:
        os.environ.pop("TELEGRAM_BOT_TOKEN", None)
        self.assertIsNone(load_telegram_config())

    def test_returns_config_with_env_allowlist(self) -> None:
        os.environ["TELEGRAM_BOT_TOKEN"] = "test-token"
        os.environ["TELEGRAM_ALLOWED_CHAT_IDS"] = "42"
        config = load_telegram_config()
        self.assertIsNotNone(config)
        assert config is not None
        self.assertEqual(config.bot_token, "test-token")
        self.assertEqual(config.allowed_chat_ids, frozenset({42}))

    def test_load_allowed_chat_ids_from_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = os.path.join(tmp, "data")
            os.makedirs(data_dir)
            config_path = os.path.join(data_dir, "telegram.json")
            with open(config_path, "w", encoding="utf-8") as handle:
                json.dump({"allowed_chat_ids": [100, 200]}, handle)
            allowed = load_allowed_chat_ids_from_file(data_dir)
            self.assertEqual(allowed, frozenset({100, 200}))


class ImageEncodingTests(unittest.TestCase):
    def test_bytes_to_jpeg_data_uri_resizes_and_prefix(self) -> None:
        jpeg_bytes = _make_jpeg_bytes(width=1600, height=900)
        data_uri = bytes_to_jpeg_data_uri(jpeg_bytes)
        self.assertTrue(data_uri.startswith("data:image/jpeg;base64,"))
        payload = data_uri.split(",", 1)[1]
        decoded = np.frombuffer(base64.b64decode(payload), dtype=np.uint8)
        frame = cv2.imdecode(decoded, cv2.IMREAD_COLOR)
        assert frame is not None
        self.assertLessEqual(frame.shape[1], DEFAULT_MAX_WIDTH)


class TelegramEnqueueTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_turn_contexts()
        self.runtime_config = RuntimeConfig()
        self.queue: Queue = Queue()

    def tearDown(self) -> None:
        reset_turn_contexts()

    def test_text_turn_registers_channel_and_enqueues_text_only_request(self) -> None:
        enqueue_telegram_text_turn(
            runtime_config=self.runtime_config,
            text_prompt_queue=self.queue,
            turn_id="tg-1",
            chat_id=555,
            text="Hello from Telegram",
        )
        context = get_turn("tg-1")
        self.assertIsNotNone(context)
        assert context is not None
        self.assertEqual(context.channel, "telegram")
        self.assertEqual(context.telegram_chat_id, 555)

        request = self.queue.get_nowait()
        self.assertIsInstance(request, GenerateResponseRequest)
        assert request.response is not None
        self.assertEqual(request.response.output_modalities, ["text"])

    def test_photo_turn_adds_multimodal_message(self) -> None:
        data_uri = bytes_to_jpeg_data_uri(_make_jpeg_bytes())
        enqueue_telegram_photo_turn(
            runtime_config=self.runtime_config,
            text_prompt_queue=self.queue,
            turn_id="tg-2",
            chat_id=777,
            image_data_uri=data_uri,
            caption="My outfit",
        )
        items = self.runtime_config.chat.to_transformers_chat()
        user_messages = [item for item in items if item.get("role") == "user"]
        self.assertTrue(user_messages)
        last = user_messages[-1]
        content = last.get("content")
        self.assertIsInstance(content, list)
        assert isinstance(content, list)
        types = {part.get("type") for part in content}
        self.assertIn("input_text", types)
        self.assertIn("input_image", types)


class ChannelReplyRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_turn_contexts()
        self.sent: list[tuple[int, str, int | None]] = []
        self.router = ChannelReplyRouter(Event(), Queue(), Queue())
        self.router.setup(send_telegram_reply=self._send)

    def tearDown(self) -> None:
        reset_turn_contexts()

    def _send(self, chat_id: int, text: str, message_thread_id: int | None) -> None:
        self.sent.append((chat_id, text, message_thread_id))

    def test_voice_turn_does_not_send_telegram_reply(self) -> None:
        chunk = LLMResponseChunk(text="Hello", turn_id="voice-1", turn_revision=0)
        list(self.router.process(chunk))
        end = EndOfResponse(turn_id="voice-1", turn_revision=0)
        list(self.router.process(end))
        self.assertEqual(self.sent, [])

    def test_telegram_turn_accumulates_and_sends_on_end(self) -> None:
        register_turn(
            "tg-99",
            TurnReplyContext(channel="telegram", telegram_chat_id=12345),
        )
        list(self.router.process(LLMResponseChunk(text="Your boots ", turn_id="tg-99", turn_revision=0)))
        list(self.router.process(LLMResponseChunk(text="look great!", turn_id="tg-99", turn_revision=0)))
        list(self.router.process(EndOfResponse(turn_id="tg-99", turn_revision=0)))
        self.assertEqual(self.sent, [(12345, "Your boots look great!", None)])
        self.assertIsNone(get_turn("tg-99"))

    def test_long_reply_splits_at_4096(self) -> None:
        register_turn(
            "tg-long",
            TurnReplyContext(channel="telegram", telegram_chat_id=1),
        )
        long_text = "x" * 5000
        list(self.router.process(LLMResponseChunk(text=long_text, turn_id="tg-long", turn_revision=0)))
        list(self.router.process(EndOfResponse(turn_id="tg-long", turn_revision=0)))
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(len(self.sent[0][1]), 4096)
        self.assertEqual(len(self.sent[1][1]), 5000 - 4096)


class ToolRoundModalityTests(unittest.TestCase):
    def test_executor_remembers_text_only_response_for_tool_follow_up(self) -> None:
        runtime_config = RuntimeConfig()
        chunk = LLMResponseChunk(
            text="",
            runtime_config=runtime_config,
            response=text_only_response_params(),
            turn_id="tg-tool",
            turn_revision=0,
        )
        executor = LocalToolExecutor(Event(), Queue(), Queue())
        executor.setup(text_prompt_queue=Queue())
        executor._remember_context(chunk)
        assert executor._pending_context is not None
        assert executor._pending_context.response is not None
        self.assertEqual(executor._pending_context.response.output_modalities, ["text"])


if __name__ == "__main__":
    unittest.main()
