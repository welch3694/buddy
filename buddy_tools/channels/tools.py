"""Cross-channel delivery tools: Telegram send and speak-aloud (#38)."""

from __future__ import annotations

import logging
from typing import Any

from openai.types.realtime import RealtimeFunctionTool
from speech_to_speech.pipeline.messages import EndOfResponse, TTSInput

from buddy_tools.channels.turn_context import get_turn, suppress_default_telegram_reply
from buddy_tools.core.consolidate import ActionSpec, build_action_tool, resolve_action_args
from buddy_tools.core.groups import ToolGroup
from buddy_tools.core.result import ToolExecutionResult
from buddy_tools.core.tool_logging import log_tool_failure, safe_tool_context, tool_error
from buddy_tools.voice.session import get_tts_handler

logger = logging.getLogger(__name__)

_CHAT_ID_PROPERTY = {
    "type": "integer",
    "description": (
        "Optional Telegram chat id. Only needed when multiple chats "
        "are allowlisted and there is no prior inbound chat."
    ),
}

CHANNEL_ACTIONS: tuple[ActionSpec, ...] = (
    ActionSpec(
        action="send_telegram_message",
        legacy_name="send_telegram_message",
        required=("text",),
        properties={
            "text": {
                "type": "string",
                "description": (
                    "Text content: message body to send for send_telegram_message, "
                    "or exact text to speak aloud for speak_aloud"
                ),
            },
            "chat_id": _CHAT_ID_PROPERTY,
        },
    ),
    ActionSpec(
        action="send_telegram_photo",
        legacy_name="send_telegram_photo",
        properties={
            "caption": {
                "type": "string",
                "description": "Optional caption for the full-resolution image file",
            },
            "chat_id": _CHAT_ID_PROPERTY,
        },
    ),
    ActionSpec(
        action="speak_aloud",
        legacy_name="speak_aloud",
        required=("text",),
    ),
)

CHANNEL_TOOL_DEFINITION: RealtimeFunctionTool = build_action_tool(
    name="channel",
    description=(
        "Cross-channel delivery operations. Use action=send_telegram_message to send text to "
        "Telegram, action=send_telegram_photo to send the latest screen/camera capture as a "
        "full-resolution Telegram file (call capture_screen or capture_camera first), or "
        "action=speak_aloud to speak exact text on the local speakers. Independent of which "
        "channel started the turn."
    ),
    actions=CHANNEL_ACTIONS,
)

CHANNEL_TOOL_DEFINITIONS: list[RealtimeFunctionTool] = [CHANNEL_TOOL_DEFINITION]
CHANNEL_TOOL_NAMES = frozenset({"channel"})


def build_channel_instructions() -> str:
    return (
        "You can deliver output to a channel other than the one that started this turn using "
        "the channel tool:\n"
        "- channel(action=send_telegram_message): send text to Telegram (e.g. summaries for easy "
        "copying). After sending, keep any spoken or same-channel reply brief — do not recite the "
        "full message again.\n"
        "- channel(action=send_telegram_photo): send the latest screen/camera capture as a "
        "full-resolution Telegram file. Call vision(action=capture_screen) or "
        "vision(action=capture_camera) first, then this action (not send_telegram_message). Keep "
        "the spoken/same-channel ack brief.\n"
        "- channel(action=speak_aloud): speak exact text on the local speakers (e.g. when Telegram "
        "says \"read this aloud\"). Keep the same-channel reply brief (a short ack).\n"
        "Do not use this tool for ordinary same-channel replies."
    )


CHANNEL_TOOL_GROUP = ToolGroup(
    id="channels",
    title="Channels",
    when_to_use=(
        "User asks to send something (text or photo) to Telegram, or to read/speak "
        "something aloud on a different channel than the current turn."
    ),
    tools=(CHANNEL_TOOL_DEFINITION,),
    instructions=build_channel_instructions(),
)


def _parse_optional_chat_id(raw: Any) -> int | None:
    if raw is None or raw == "":
        return None
    return int(raw)


def _send_telegram_message(
    args: dict[str, Any],
    *,
    turn_id: str | None,
) -> ToolExecutionResult:
    # Lazy imports avoid circular import: registry → tools → telegram → bootstrap → registry
    from buddy_tools.channels.reply_router import TELEGRAM_MAX_MESSAGE_LENGTH
    from buddy_tools.channels.telegram import get_telegram_bridge, resolve_outbound_chat

    text = str(args.get("text", "")).strip()
    if not text:
        return tool_error(
            "send_telegram_message",
            "text is required",
            context=safe_tool_context(args),
        )

    bridge = get_telegram_bridge()
    if bridge is None:
        return tool_error(
            "send_telegram_message",
            "Telegram bridge is not configured",
            context=safe_tool_context(args),
        )

    try:
        explicit_chat_id = _parse_optional_chat_id(args.get("chat_id"))
        chat_id, thread_id = resolve_outbound_chat(
            turn_id=turn_id,
            chat_id=explicit_chat_id,
        )
    except (TypeError, ValueError) as exc:
        return tool_error(
            "send_telegram_message",
            str(exc),
            context=safe_tool_context(args),
        )

    try:
        for offset in range(0, len(text), TELEGRAM_MAX_MESSAGE_LENGTH):
            chunk = text[offset : offset + TELEGRAM_MAX_MESSAGE_LENGTH]
            bridge.send_reply(chat_id, chunk, thread_id)
    except Exception as exc:
        log_tool_failure(
            "send_telegram_message",
            f"send failed: {exc}",
            exc=exc,
            context={**(safe_tool_context(args) or {}), "chat_id": chat_id},
        )
        return ToolExecutionResult(output=f"Error: send failed: {exc}")

    turn = get_turn(turn_id)
    if turn is not None and turn.channel == "telegram":
        suppress_default_telegram_reply(turn_id)

    logger.info(
        "send_telegram_message delivered %d chars to chat_id=%s turn=%s",
        len(text),
        chat_id,
        turn_id,
    )
    return ToolExecutionResult(output=f"Sent Telegram message to chat {chat_id}.")


TELEGRAM_CAPTION_MAX_LENGTH = 1024


def _send_telegram_photo(
    args: dict[str, Any],
    *,
    turn_id: str | None,
) -> ToolExecutionResult:
    from buddy_tools.channels.last_capture import get_last_capture_delivery
    from buddy_tools.channels.telegram import get_telegram_bridge, resolve_outbound_chat

    delivery = get_last_capture_delivery()
    if not delivery:
        return tool_error(
            "send_telegram_photo",
            "no recent capture available; call capture_screen or capture_camera first",
            context=safe_tool_context(args),
        )
    jpeg_bytes, filename = delivery

    bridge = get_telegram_bridge()
    if bridge is None:
        return tool_error(
            "send_telegram_photo",
            "Telegram bridge is not configured",
            context=safe_tool_context(args),
        )

    try:
        explicit_chat_id = _parse_optional_chat_id(args.get("chat_id"))
        chat_id, thread_id = resolve_outbound_chat(
            turn_id=turn_id,
            chat_id=explicit_chat_id,
        )
    except (TypeError, ValueError) as exc:
        return tool_error(
            "send_telegram_photo",
            str(exc),
            context=safe_tool_context(args),
        )

    caption_raw = args.get("caption")
    caption: str | None = None
    if caption_raw is not None:
        caption = str(caption_raw).strip() or None
        if caption is not None and len(caption) > TELEGRAM_CAPTION_MAX_LENGTH:
            caption = caption[:TELEGRAM_CAPTION_MAX_LENGTH]

    try:
        bridge.send_document(
            chat_id,
            jpeg_bytes,
            filename=filename,
            caption=caption,
            message_thread_id=thread_id,
        )
    except Exception as exc:
        log_tool_failure(
            "send_telegram_photo",
            f"send failed: {exc}",
            exc=exc,
            context={**(safe_tool_context(args) or {}), "chat_id": chat_id},
        )
        return ToolExecutionResult(output=f"Error: send failed: {exc}")

    turn = get_turn(turn_id)
    if turn is not None and turn.channel == "telegram":
        suppress_default_telegram_reply(turn_id)

    logger.info(
        "send_telegram_photo delivered %d bytes (%s) to chat_id=%s turn=%s",
        len(jpeg_bytes),
        filename,
        chat_id,
        turn_id,
    )
    return ToolExecutionResult(output=f"Sent Telegram photo to chat {chat_id}.")


def _speak_aloud(
    args: dict[str, Any],
    *,
    turn_id: str | None,
    turn_revision: int | None,
) -> ToolExecutionResult:
    text = str(args.get("text", "")).strip()
    if not text:
        return tool_error(
            "speak_aloud",
            "text is required",
            context=safe_tool_context(args),
        )

    handler = get_tts_handler()
    if handler is None:
        return tool_error(
            "speak_aloud",
            "TTS handler is not available",
            context=safe_tool_context(args),
        )

    queue_in = getattr(handler, "queue_in", None)
    if queue_in is None:
        return tool_error(
            "speak_aloud",
            "TTS handler has no input queue",
            context=safe_tool_context(args),
        )

    try:
        queue_in.put(
            TTSInput(
                text=text,
                turn_id=turn_id,
                turn_revision=turn_revision,
            )
        )
        queue_in.put(
            EndOfResponse(
                turn_id=turn_id,
                turn_revision=turn_revision,
            )
        )
    except Exception as exc:
        log_tool_failure(
            "speak_aloud",
            f"TTS enqueue failed: {exc}",
            exc=exc,
            context=safe_tool_context(args),
        )
        return ToolExecutionResult(output=f"Error: TTS enqueue failed: {exc}")

    logger.info("speak_aloud enqueued %d chars turn=%s", len(text), turn_id)
    return ToolExecutionResult(output="Speaking aloud on local speakers.")


def execute_channel_tool(
    tool_name: str,
    args: dict[str, Any],
    *,
    turn_id: str | None = None,
    turn_revision: int | None = None,
) -> ToolExecutionResult:
    if tool_name == "channel":
        resolved = resolve_action_args("channel", args, CHANNEL_ACTIONS)
        if isinstance(resolved, ToolExecutionResult):
            return resolved
        tool_name, args = resolved

    if tool_name == "send_telegram_message":
        return _send_telegram_message(args, turn_id=turn_id)
    if tool_name == "send_telegram_photo":
        return _send_telegram_photo(args, turn_id=turn_id)
    if tool_name == "speak_aloud":
        return _speak_aloud(args, turn_id=turn_id, turn_revision=turn_revision)
    return tool_error(tool_name, f"unknown channel tool {tool_name!r}")
