"""Telegram long-polling bridge for shared Buddy session context."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from queue import Queue
from typing import Any

from openai.types.realtime import RealtimeConversationItemUserMessage
from openai.types.realtime.realtime_conversation_item_user_message import Content as UserContent
from openai.types.realtime.realtime_response_create_params import RealtimeResponseCreateParams

from speech_to_speech.LLM.chat import make_user_message
from speech_to_speech.pipeline.messages import GenerateResponseRequest

from buddy_tools.channels.images import bytes_to_jpeg_data_uri
from buddy_tools.channels.turn_context import TurnReplyContext, register_turn
from buddy_tools.episodic import EpisodicTurnRecord, get_episodic_manager
from buddy_tools.infra.data_dir import get_data_dir

logger = logging.getLogger(__name__)

_ENV_BOT_TOKEN = "TELEGRAM_BOT_TOKEN"
_ENV_ALLOWED_CHAT_IDS = "TELEGRAM_ALLOWED_CHAT_IDS"
_CONFIG_FILENAME = "telegram.json"

DEFAULT_PHOTO_CAPTION = "Photo from Telegram."


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str
    allowed_chat_ids: frozenset[int]


_telegram_bridge: TelegramBridge | None = None


def get_telegram_bridge() -> TelegramBridge | None:
    return _telegram_bridge


def set_telegram_bridge(bridge: TelegramBridge | None) -> None:
    global _telegram_bridge
    _telegram_bridge = bridge


def parse_allowed_chat_ids(raw: str) -> frozenset[int]:
    """Parse comma-separated chat IDs."""
    ids: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        ids.add(int(part))
    return frozenset(ids)


def load_allowed_chat_ids_from_file(data_dir: Path | str | None = None) -> frozenset[int]:
    """Load allowed chat IDs from {BUDDY_DATA_DIR}/telegram.json."""
    root = Path(data_dir) if data_dir is not None else get_data_dir()
    root = root.resolve()
    config_path = root / _CONFIG_FILENAME
    if not config_path.is_file():
        return frozenset()

    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not read %s: %s", config_path, exc)
        return frozenset()

    raw_ids = payload.get("allowed_chat_ids", [])
    if not isinstance(raw_ids, list):
        return frozenset()
    return frozenset(int(chat_id) for chat_id in raw_ids)


def load_telegram_config(data_dir: Path | None = None) -> TelegramConfig | None:
    """Return Telegram config when a bot token is set; otherwise None."""
    token = os.environ.get(_ENV_BOT_TOKEN, "").strip()
    if not token:
        return None

    env_raw = os.environ.get(_ENV_ALLOWED_CHAT_IDS, "").strip()
    if env_raw:
        allowed = parse_allowed_chat_ids(env_raw)
    else:
        allowed = load_allowed_chat_ids_from_file(data_dir)

    if not allowed:
        logger.warning(
            "TELEGRAM_BOT_TOKEN is set but no allowed chat IDs found "
            "(set %s or %s in the data dir)",
            _ENV_ALLOWED_CHAT_IDS,
            _CONFIG_FILENAME,
        )
        return None

    return TelegramConfig(bot_token=token, allowed_chat_ids=allowed)


def is_chat_allowed(chat_id: int, allowed_chat_ids: frozenset[int]) -> bool:
    return chat_id in allowed_chat_ids


def text_only_response_params() -> RealtimeResponseCreateParams:
    return RealtimeResponseCreateParams(output_modalities=["text"])


def build_telegram_generate_request(
    *,
    runtime_config: Any,
    turn_id: str,
) -> GenerateResponseRequest:
    return GenerateResponseRequest(
        runtime_config=runtime_config,
        response=text_only_response_params(),
        turn_id=turn_id,
        turn_revision=0,
    )


def _log_telegram_user_turn(
    *,
    turn_id: str,
    text: str,
    content_type: str = "text",
    has_image: bool = False,
) -> None:
    manager = get_episodic_manager()
    if manager is None:
        return
    manager.on_user_activity("telegram")
    manager.log_turn(
        EpisodicTurnRecord(
            role="user",
            channel="telegram",
            turn_id=turn_id,
            text=text,
            content_type=content_type,  # type: ignore[arg-type]
            has_image=has_image,
        )
    )


def _record_inbound_chat(chat_id: int, message_thread_id: int | None = None) -> None:
    bridge = get_telegram_bridge()
    if bridge is not None:
        bridge.record_inbound(chat_id, message_thread_id)


def resolve_outbound_chat(
    *,
    turn_id: str | None = None,
    chat_id: int | None = None,
) -> tuple[int, int | None]:
    """Resolve (chat_id, thread_id) for an outbound Telegram send.

    Order: explicit allowlisted chat_id → current turn → last inbound → sole allowlist entry.
    Raises ValueError when unresolved or not allowlisted.
    """
    from buddy_tools.channels.turn_context import get_turn

    bridge = get_telegram_bridge()
    if bridge is None:
        raise ValueError("Telegram bridge is not configured")

    allowed = bridge.config.allowed_chat_ids
    thread_id: int | None = None

    if chat_id is not None:
        if not is_chat_allowed(chat_id, allowed):
            raise ValueError(f"chat_id {chat_id} is not allowlisted")
        turn = get_turn(turn_id)
        if turn is not None and turn.telegram_chat_id == chat_id:
            thread_id = turn.telegram_message_thread_id
        elif bridge.last_inbound_chat_id == chat_id:
            thread_id = bridge.last_inbound_message_thread_id
        return chat_id, thread_id

    turn = get_turn(turn_id)
    if turn is not None and turn.telegram_chat_id is not None:
        if not is_chat_allowed(turn.telegram_chat_id, allowed):
            raise ValueError(f"chat_id {turn.telegram_chat_id} is not allowlisted")
        return turn.telegram_chat_id, turn.telegram_message_thread_id

    if bridge.last_inbound_chat_id is not None:
        if not is_chat_allowed(bridge.last_inbound_chat_id, allowed):
            raise ValueError(f"chat_id {bridge.last_inbound_chat_id} is not allowlisted")
        return bridge.last_inbound_chat_id, bridge.last_inbound_message_thread_id

    if len(allowed) == 1:
        sole = next(iter(allowed))
        return sole, None

    raise ValueError(
        "Could not resolve Telegram chat_id: provide chat_id, or ensure a prior "
        "inbound message / single allowlisted chat"
    )


def enqueue_telegram_text_turn(
    *,
    runtime_config: Any,
    text_prompt_queue: Queue[Any],
    turn_id: str,
    chat_id: int,
    text: str,
    message_thread_id: int | None = None,
) -> None:
    """Register a Telegram text turn, append to chat, and trigger generation."""
    register_turn(
        turn_id,
        TurnReplyContext(
            channel="telegram",
            telegram_chat_id=chat_id,
            telegram_message_thread_id=message_thread_id,
        ),
    )
    _record_inbound_chat(chat_id, message_thread_id)
    _log_telegram_user_turn(turn_id=turn_id, text=text)
    runtime_config.chat.add_item(make_user_message(text))
    text_prompt_queue.put(
        build_telegram_generate_request(runtime_config=runtime_config, turn_id=turn_id)
    )


def enqueue_telegram_photo_turn(
    *,
    runtime_config: Any,
    text_prompt_queue: Queue[Any],
    turn_id: str,
    chat_id: int,
    image_data_uri: str,
    caption: str | None = None,
    message_thread_id: int | None = None,
) -> None:
    """Register a Telegram photo turn, append multimodal message, and trigger generation."""
    register_turn(
        turn_id,
        TurnReplyContext(
            channel="telegram",
            telegram_chat_id=chat_id,
            telegram_message_thread_id=message_thread_id,
        ),
    )
    _record_inbound_chat(chat_id, message_thread_id)
    caption_text = (caption or "").strip() or DEFAULT_PHOTO_CAPTION
    _log_telegram_user_turn(
        turn_id=turn_id,
        text=caption_text,
        content_type="photo",
        has_image=True,
    )
    image_msg = RealtimeConversationItemUserMessage(
        type="message",
        role="user",
        content=[
            UserContent(type="input_text", text=caption_text),
            UserContent(
                type="input_image",
                image_url=image_data_uri,
                detail="auto",
            ),
        ],
    )
    runtime_config.chat.add_item(image_msg)
    text_prompt_queue.put(
        build_telegram_generate_request(runtime_config=runtime_config, turn_id=turn_id)
    )


class TelegramBridge:
    """Long-polling Telegram bot tied to the Buddy speech-to-speech session."""

    def __init__(
        self,
        config: TelegramConfig,
        *,
        runtime_config: Any,
        text_prompt_queue: Queue[Any],
        stop_event: threading.Event,
    ) -> None:
        self.config = config
        self.runtime_config = runtime_config
        self.text_prompt_queue = text_prompt_queue
        self.stop_event = stop_event
        self.last_inbound_chat_id: int | None = None
        self.last_inbound_message_thread_id: int | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._application: Any = None
        self._thread: threading.Thread | None = None

    def record_inbound(self, chat_id: int, message_thread_id: int | None = None) -> None:
        self.last_inbound_chat_id = chat_id
        self.last_inbound_message_thread_id = message_thread_id

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run_polling, name="telegram-bridge", daemon=True)
        self._thread.start()
        logger.info(
            "Telegram bridge started (allowed chat IDs: %s)",
            ", ".join(str(chat_id) for chat_id in sorted(self.config.allowed_chat_ids)),
        )

    def send_reply(self, chat_id: int, text: str, message_thread_id: int | None = None) -> None:
        if self._loop is None or self._application is None:
            logger.warning("Telegram bridge not ready; dropping reply")
            return
        future = asyncio.run_coroutine_threadsafe(
            self._send_message(chat_id, text, message_thread_id),
            self._loop,
        )
        try:
            future.result(timeout=30)
        except Exception:
            logger.exception("Telegram send_message failed for chat_id=%s", chat_id)
            raise

    async def _send_message(self, chat_id: int, text: str, message_thread_id: int | None) -> None:
        kwargs: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if message_thread_id is not None:
            kwargs["message_thread_id"] = message_thread_id
        await self._application.bot.send_message(**kwargs)

    def _run_polling(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._async_main())
        except Exception:
            logger.exception("Telegram bridge exited with an error")
        finally:
            loop.close()
            self._loop = None
            self._application = None

    async def _async_main(self) -> None:
        from telegram.ext import Application, MessageHandler, filters

        application = Application.builder().token(self.config.bot_token).build()
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_text))
        application.add_handler(MessageHandler(filters.PHOTO, self._on_photo))
        self._application = application

        await application.initialize()
        await application.start()
        await application.updater.start_polling(drop_pending_updates=True)
        logger.info("Telegram polling active")

        while not self.stop_event.is_set():
            await asyncio.sleep(0.5)

        await application.updater.stop()
        await application.stop()
        await application.shutdown()
        logger.info("Telegram bridge stopped")

    async def _on_text(self, update: Any, context: Any) -> None:
        message = update.effective_message
        if message is None or not message.text:
            return
        chat_id = message.chat_id
        if not is_chat_allowed(chat_id, self.config.allowed_chat_ids):
            logger.debug("Ignoring Telegram text from unauthorized chat_id=%s", chat_id)
            return

        turn_id = f"tg-{update.update_id}"
        thread_id = getattr(message, "message_thread_id", None)
        try:
            enqueue_telegram_text_turn(
                runtime_config=self.runtime_config,
                text_prompt_queue=self.text_prompt_queue,
                turn_id=turn_id,
                chat_id=chat_id,
                text=message.text,
                message_thread_id=thread_id,
            )
            logger.info("Queued Telegram text turn %s from chat_id=%s", turn_id, chat_id)
        except Exception:
            logger.exception("Failed to enqueue Telegram text turn")

    async def _on_photo(self, update: Any, context: Any) -> None:
        message = update.effective_message
        if message is None or not message.photo:
            return
        chat_id = message.chat_id
        if not is_chat_allowed(chat_id, self.config.allowed_chat_ids):
            logger.debug("Ignoring Telegram photo from unauthorized chat_id=%s", chat_id)
            return

        turn_id = f"tg-{update.update_id}"
        thread_id = getattr(message, "message_thread_id", None)
        caption = message.caption

        try:
            photo = message.photo[-1]
            telegram_file = await context.bot.get_file(photo.file_id)
            image_bytes = bytes(await telegram_file.download_as_bytearray())
            data_uri = bytes_to_jpeg_data_uri(image_bytes)
            enqueue_telegram_photo_turn(
                runtime_config=self.runtime_config,
                text_prompt_queue=self.text_prompt_queue,
                turn_id=turn_id,
                chat_id=chat_id,
                image_data_uri=data_uri,
                caption=caption,
                message_thread_id=thread_id,
            )
            logger.info("Queued Telegram photo turn %s from chat_id=%s", turn_id, chat_id)
        except Exception:
            logger.exception("Failed to enqueue Telegram photo turn")


def create_and_start_telegram_bridge(
    *,
    runtime_config: Any,
    text_prompt_queue: Queue[Any],
    stop_event: threading.Event,
    data_dir: Path | None = None,
) -> TelegramBridge | None:
    """Create and start the Telegram bridge when configured."""
    config = load_telegram_config(data_dir)
    if config is None:
        return None

    bridge = TelegramBridge(
        config,
        runtime_config=runtime_config,
        text_prompt_queue=text_prompt_queue,
        stop_event=stop_event,
    )
    bridge.start()
    set_telegram_bridge(bridge)
    return bridge
