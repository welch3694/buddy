"""Route LLM replies to the channel that originated each turn."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from typing import Any

from speech_to_speech.baseHandler import BaseHandler
from speech_to_speech.pipeline.handler_types import LLMOut
from speech_to_speech.pipeline.messages import EndOfResponse, LLMResponseChunk
from speech_to_speech.pipeline.speculative_turns import SpeculativeTurnTracker

from buddy_tools.channels.turn_context import clear_turn, get_turn

logger = logging.getLogger(__name__)

TELEGRAM_MAX_MESSAGE_LENGTH = 4096


class ChannelReplyRouter(BaseHandler[LLMOut, LLMOut]):
    """Accumulates text for Telegram turns and delivers replies on EndOfResponse."""

    def setup(
        self,
        send_telegram_reply: Callable[[int, str, int | None], None] | None = None,
        speculative_turns: SpeculativeTurnTracker | None = None,
    ) -> None:
        self.send_telegram_reply = send_telegram_reply
        self.speculative_turns = speculative_turns
        self._accumulated_text: dict[str, list[str]] = {}

    def _turn_output_allowed(self, turn_id: str | None, turn_revision: int | None) -> bool:
        if self.speculative_turns is None:
            return True
        return self.speculative_turns.is_latest_after_reopen_grace(turn_id, turn_revision)

    def _accumulate(self, turn_id: str | None, text: str) -> None:
        if not turn_id or not text:
            return
        self._accumulated_text.setdefault(turn_id, []).append(text)

    def _pop_accumulated(self, turn_id: str | None) -> str:
        if turn_id is None:
            return ""
        parts = self._accumulated_text.pop(turn_id, [])
        return "".join(parts).strip()

    def _deliver_telegram_reply(self, turn_id: str | None) -> None:
        if turn_id is None or self.send_telegram_reply is None:
            return

        context = get_turn(turn_id)
        if context is None or context.channel != "telegram":
            return
        if context.telegram_chat_id is None:
            clear_turn(turn_id)
            return

        text = self._pop_accumulated(turn_id)
        clear_turn(turn_id)
        if not text:
            return

        chat_id = context.telegram_chat_id
        thread_id = context.telegram_message_thread_id
        for offset in range(0, len(text), TELEGRAM_MAX_MESSAGE_LENGTH):
            chunk = text[offset : offset + TELEGRAM_MAX_MESSAGE_LENGTH]
            try:
                self.send_telegram_reply(chat_id, chunk, thread_id)
            except Exception:
                logger.exception("Failed to send Telegram reply for turn %s", turn_id)

    def process(self, lm_output: LLMOut) -> Iterator[LLMOut]:
        if isinstance(lm_output, LLMResponseChunk):
            if not self._turn_output_allowed(lm_output.turn_id, lm_output.turn_revision):
                return
            context = get_turn(lm_output.turn_id)
            if context is not None and context.channel == "telegram":
                self._accumulate(lm_output.turn_id, lm_output.text)
            yield lm_output
            return

        if isinstance(lm_output, EndOfResponse):
            if not self._turn_output_allowed(lm_output.turn_id, lm_output.turn_revision):
                return
            self._deliver_telegram_reply(lm_output.turn_id)
            yield lm_output
            return

        yield lm_output
