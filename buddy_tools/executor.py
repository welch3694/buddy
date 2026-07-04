"""Execute local tool calls and trigger LLM follow-up."""

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

from buddy_tools.personality import get_active_personality
from buddy_tools.personality_session import apply_personality_switch
from buddy_tools.registry import execute_tool, refresh_session_instructions
from buddy_tools.result import ToolExecutionResult
from buddy_tools.tool_logging import is_tool_error
from buddy_tools.timers import cancel_all_timers
from buddy_tools.voice_session import apply_voice

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 5


def _log_tool_result(tool_name: str, result: ToolExecutionResult) -> None:
    if is_tool_error(result):
        logger.error("Tool %s failed: %s", tool_name, result.output)
    else:
        logger.info("Tool %s succeeded: %s", tool_name, result.output[:120])


class LocalToolExecutor(BaseHandler[LLMOut, LLMOut]):
    """Intercepts LLM output, runs local tools, and re-queues follow-up generations."""

    def setup(
        self,
        text_prompt_queue: Queue[LLMIn] | None = None,
        memory_root: str | Path | None = None,
        persona_namespace: str | None = None,
        speculative_turns: SpeculativeTurnTracker | None = None,
        *,
        memory_dir: str | Path | None = None,
    ) -> None:
        self.text_prompt_queue = text_prompt_queue
        legacy_root = memory_dir or memory_root
        self.memory_root = Path(legacy_root) if legacy_root else Path("memory")
        self.persona_namespace = persona_namespace or "buddy"
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
            pending_names = [tool.name for tool in self._pending_tools]
            logger.warning(
                "Max tool rounds (%d) reached; skipping %d tools: %s",
                MAX_TOOL_ROUNDS,
                len(pending_names),
                pending_names,
            )
            self._pending_tools.clear()
            return False

        runtime_config: RuntimeConfig = self._pending_context.runtime_config
        chat = runtime_config.chat

        for tool in self._pending_tools:
            result = execute_tool(
                self.memory_root,
                tool.name,
                tool.arguments,
                persona_namespace=self.persona_namespace,
            )
            _log_tool_result(tool.name, result)
            skip_chat_record = False

            if result.personality_switch_id:
                try:
                    profile = apply_personality_switch(
                        result.personality_switch_id,
                        runtime_config=runtime_config,
                        chat=chat,
                        memory_root=self.memory_root,
                    )
                    self.persona_namespace = profile.memory_namespace
                    result = ToolExecutionResult(output=f"Now speaking as {profile.name}.")
                    # Chat was reset; the function_call is gone so tool output cannot be paired.
                    skip_chat_record = True
                except (FileNotFoundError, ValueError, OSError) as exc:
                    logger.exception("Tool %s: personality switch failed", tool.name)
                    result = ToolExecutionResult(output=f"Error: could not switch personality: {exc}")

            if result.voice_switch_id:
                try:
                    voice_profile = apply_voice(result.voice_switch_id, runtime_config=runtime_config)
                    result = ToolExecutionResult(output=f"Now using the {voice_profile.id} voice.")
                except (FileNotFoundError, ValueError) as exc:
                    logger.exception("Tool %s: voice switch failed", tool.name)
                    result = ToolExecutionResult(output=f"Error: could not switch voice: {exc}")

            if result.refresh_instructions:
                try:
                    profile = get_active_personality()
                    refresh_session_instructions(
                        runtime_config,
                        memory_root=self.memory_root,
                        persona_namespace=self.persona_namespace,
                        personality_id=profile.id,
                        include_full_skill_body=result.include_full_skill_body,
                    )
                except (FileNotFoundError, ValueError, OSError) as exc:
                    logger.exception("Session instruction refresh failed")
                    logger.warning("Could not refresh instructions after %s: %s", tool.name, exc)

            if not skip_chat_record:
                output_item = RealtimeConversationItemFunctionCallOutput(
                    type="function_call_output",
                    call_id=tool.call_id,
                    output=result.output,
                    status="completed",
                )
                try:
                    chat.add_item(output_item)
                except ChatItemError as exc:
                    logger.error("Could not record tool output for %s: %s", tool.call_id, exc)

            if result.image_data_uri:
                caption = result.image_caption or "Here is the captured image."
                image_msg = RealtimeConversationItemUserMessage(
                    type="message",
                    role="user",
                    content=[
                        UserContent(type="input_text", text=caption),
                        UserContent(
                            type="input_image",
                            image_url=result.image_data_uri,
                            detail="auto",
                        ),
                    ],
                )
                try:
                    chat.add_item(image_msg)
                    logger.info("Injected image into chat for tool %s", tool.name)
                except ChatItemError as exc:
                    logger.error("Could not inject image for %s: %s", tool.call_id, exc)

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
        cancel_all_timers()
        self._pending_tools.clear()
        self._pending_context = None
        self._tool_rounds = 0
