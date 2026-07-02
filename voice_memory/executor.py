"""Execute memory tool calls in local mode and trigger LLM follow-up."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path
from queue import Queue
from typing import Any

from openai.types.realtime import RealtimeConversationItemFunctionCallOutput, RealtimeConversationItemUserMessage
from openai.types.realtime.realtime_conversation_item_user_message import Content as UserContent
from openai.types.responses.response_function_tool_call import ResponseFunctionToolCall

from speech_to_speech.api.openai_realtime.runtime_config import RuntimeConfig
from speech_to_speech.baseHandler import BaseHandler
from speech_to_speech.LLM.chat import ChatItemError
from speech_to_speech.pipeline.handler_types import LLMIn, LLMOut, TTSIn
from speech_to_speech.pipeline.messages import EndOfResponse, GenerateResponseRequest, LLMResponseChunk
from speech_to_speech.pipeline.speculative_turns import SpeculativeTurnTracker

from voice_memory.tools import execute_tool

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 5


class MemoryToolExecutor(BaseHandler[LLMOut, LLMOut]):
    """Intercepts LLM output, runs memory tools locally, and re-queues follow-up generations."""

    def setup(
        self,
        text_prompt_queue: Queue[LLMIn] | None = None,
        memory_dir: str | Path | None = None,
        speculative_turns: SpeculativeTurnTracker | None = None,
    ) -> None:
        self.text_prompt_queue = text_prompt_queue
        self.memory_dir = Path(memory_dir) if memory_dir else Path("memory")
        self.speculative_turns = speculative_turns
        self._pending_tools: list[ResponseFunctionToolCall] = []
        self._pending_context: GenerateResponseRequest | None = None
        self._tool_rounds = 0

    def _turn_output_allowed(self, turn_id: str | None, turn_revision: int | None) -> bool:
        if self.speculative_turns is None:
            return True
        return self.speculative_turns.is_latest_after_reopen_grace(turn_id, turn_revision)

    def _remember_context(self, chunk: LLMResponseChunk) -> None:
        self._pending_context = GenerateResponseRequest(
            runtime_config=chunk.runtime_config,
            response=chunk.response,
            language_code=chunk.language_code,
            turn_id=chunk.turn_id,
            turn_revision=chunk.turn_revision,
            speech_stopped_at_s=chunk.speech_stopped_at_s,
        )

    def _execute_pending_tools(self) -> bool:
        if not self._pending_tools or self._pending_context is None or self.text_prompt_queue is None:
            return False

        if self._tool_rounds >= MAX_TOOL_ROUNDS:
            logger.warning("Max tool rounds (%d) reached; skipping further tool execution", MAX_TOOL_ROUNDS)
            self._pending_tools.clear()
            return False

        runtime_config: RuntimeConfig = self._pending_context.runtime_config
        chat = runtime_config.chat

        for tool in self._pending_tools:
            result = execute_tool(self.memory_dir, tool.name, tool.arguments)
            logger.info("Executed tool %s -> %s", tool.name, result.output[:120])
            output_item = RealtimeConversationItemFunctionCallOutput(
                type="function_call_output",
                call_id=tool.call_id,
                output=result.output,
                status="completed",
            )
            try:
                chat.append_tool_output(tool.call_id, output_item)
            except ChatItemError as exc:
                logger.error("Could not append tool output for %s: %s", tool.call_id, exc)
                try:
                    chat.add_item(output_item)
                except ChatItemError:
                    logger.exception("Failed to record tool output for %s", tool.call_id)

            if result.image_data_uri:
                image_msg = RealtimeConversationItemUserMessage(
                    type="message",
                    role="user",
                    content=[
                        UserContent(type="input_text", text="Here is what the camera sees."),
                        UserContent(
                            type="input_image",
                            image_url=result.image_data_uri,
                            detail="auto",
                        ),
                    ],
                )
                try:
                    chat.add_item(image_msg)
                    logger.info("Injected camera image into chat for tool %s", tool.name)
                except ChatItemError as exc:
                    logger.error("Could not inject camera image for %s: %s", tool.call_id, exc)

        self._pending_tools.clear()
        self._tool_rounds += 1
        self.text_prompt_queue.put(self._pending_context)
        logger.info("Queued LLM follow-up after tool execution (round %d)", self._tool_rounds)
        return True

    def process(self, lm_output: LLMOut) -> Iterator[LLMOut]:
        if isinstance(lm_output, LLMResponseChunk):
            if not self._turn_output_allowed(lm_output.turn_id, lm_output.turn_revision):
                return
            self._remember_context(lm_output)
            if lm_output.tools:
                self._pending_tools.extend(lm_output.tools)
            yield lm_output
            return

        if isinstance(lm_output, EndOfResponse):
            if not self._turn_output_allowed(lm_output.turn_id, lm_output.turn_revision):
                return

            if self._pending_tools and self._execute_pending_tools():
                return

            self._tool_rounds = 0
            self._pending_context = None
            yield lm_output
            return

        yield lm_output

    def on_session_end(self) -> None:
        self._pending_tools.clear()
        self._pending_context = None
        self._tool_rounds = 0
