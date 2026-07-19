"""Execute local tool calls and trigger LLM follow-up."""

from __future__ import annotations

import json
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

from buddy_tools.pulse.inject import (
    handle_pulse_end_of_response,
    handle_pulse_response_chunk,
    record_assistant_speech_for_active_pulse,
)
from buddy_tools.personality import get_active_personality
from buddy_tools.personality.session import apply_personality_switch
from buddy_tools.core.registry import execute_tool, refresh_session_instructions
from buddy_tools.core.result import ToolExecutionResult
from buddy_tools.core.tool_logging import is_tool_error, safe_tool_context
from buddy_tools.timers import cancel_all_timers
from buddy_tools.episodic import (
    EpisodicTurnRecord,
    get_episodic_manager,
    reconfigure_episodic_persona,
)
from buddy_tools.episodic.turns import truncate_tool_output
from buddy_tools.channels.turn_context import get_turn
from buddy_tools.voice.session import apply_voice

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 5
_SKIPPED_TOOL_OUTPUT = (
    "Error: tool round limit reached; this tool was not executed. "
    "Summarize what you have and tell the user you could not finish looking up memory."
)


def _log_tool_result(tool_name: str, result: ToolExecutionResult) -> None:
    if is_tool_error(result):
        logger.error("Tool %s failed: %s", tool_name, result.output)
    else:
        logger.info("Tool %s succeeded: %s", tool_name, result.output[:120])


def _parse_tool_arguments(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, dict):
        return parsed
    return None


def _resolve_turn_channel(turn_id: str | None) -> str:
    ctx = get_turn(turn_id)
    if ctx is not None:
        return ctx.channel
    return "voice"


def _log_episodic_tool_turn(
    turn_id: str | None,
    tool: ResponseFunctionToolCall,
    result: ToolExecutionResult,
) -> None:
    if turn_id is None:
        return
    manager = get_episodic_manager()
    if manager is None:
        return
    raw_args = tool.arguments if isinstance(tool.arguments, str) else None
    manager.log_turn(
        EpisodicTurnRecord(
            role="tool",
            channel=_resolve_turn_channel(turn_id),  # type: ignore[arg-type]
            turn_id=turn_id,
            tool_name=tool.name,
            tool_args=safe_tool_context(_parse_tool_arguments(raw_args)),
            tool_success=not is_tool_error(result),
            tool_output_preview=truncate_tool_output(result.output),
        )
    )


def _log_episodic_assistant_turn(turn_id: str | None, text: str) -> None:
    if turn_id is None or not text:
        return
    manager = get_episodic_manager()
    if manager is None:
        return
    manager.log_turn(
        EpisodicTurnRecord(
            role="assistant",
            channel=_resolve_turn_channel(turn_id),  # type: ignore[arg-type]
            turn_id=turn_id,
            text=text,
        )
    )


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
        self._turn_text_buffer: list[str] = []

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

    def _inject_skipped_tool_errors_and_follow_up(self) -> bool:
        if not self._pending_tools or self._pending_context is None or self.text_prompt_queue is None:
            return False

        runtime_config: RuntimeConfig = self._pending_context.runtime_config
        chat = runtime_config.chat
        pending = list(self._pending_tools)
        pending_names = [tool.name for tool in pending]
        logger.warning(
            "Max tool rounds (%d) reached; skipping %d tools: %s",
            MAX_TOOL_ROUNDS,
            len(pending_names),
            pending_names,
        )

        for tool in pending:
            output_item = RealtimeConversationItemFunctionCallOutput(
                type="function_call_output",
                call_id=tool.call_id,
                output=_SKIPPED_TOOL_OUTPUT,
                status="completed",
            )
            try:
                chat.add_item(output_item)
            except ChatItemError as exc:
                logger.error("Could not record skipped tool output for %s: %s", tool.call_id, exc)

        self._pending_tools.clear()
        self.text_prompt_queue.put(self._pending_context)
        logger.info(
            "Queued LLM follow-up after skipping %d tools at round limit",
            len(pending_names),
        )
        return True

    def _execute_pending_tools(self) -> bool:
        if not self._pending_tools or self._pending_context is None or self.text_prompt_queue is None:
            return False

        if self._tool_rounds >= MAX_TOOL_ROUNDS:
            return self._inject_skipped_tool_errors_and_follow_up()

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
            _log_episodic_tool_turn(self._pending_context.turn_id, tool, result)
            skip_chat_record = False

            if result.personality_switch_id:
                episodic_manager = get_episodic_manager()
                if episodic_manager is not None:
                    episodic_manager.close_for_personality_switch()
                try:
                    profile = apply_personality_switch(
                        result.personality_switch_id,
                        runtime_config=runtime_config,
                        chat=chat,
                        memory_root=self.memory_root,
                    )
                    self.persona_namespace = profile.memory_namespace
                    reconfigure_episodic_persona(profile.memory_namespace)
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
            if lm_output.text:
                self._turn_text_buffer.append(lm_output.text)
            pulse_chunk = handle_pulse_response_chunk(lm_output)
            if pulse_chunk is None:
                return
            if pulse_chunk.text:
                from buddy_tools.companion.publisher import emit_assistant_text

                emit_assistant_text(
                    pulse_chunk.text,
                    turn_id=pulse_chunk.turn_id,
                    turn_revision=pulse_chunk.turn_revision,
                )
            yield pulse_chunk
            return

        if isinstance(lm_output, EndOfResponse):
            if not self._turn_output_allowed(lm_output.turn_id, lm_output.turn_revision):
                return

            if self._pending_tools and self._execute_pending_tools():
                self._turn_text_buffer.clear()
                return

            full_text = "".join(self._turn_text_buffer).strip()
            self._turn_text_buffer.clear()
            handle_pulse_end_of_response()
            record_assistant_speech_for_active_pulse(full_text)
            _log_episodic_assistant_turn(lm_output.turn_id, full_text)

            self._tool_rounds = 0
            self._pending_context = None
            yield lm_output
            return

        yield lm_output

    def on_session_end(self) -> None:
        cancel_all_timers()
        manager = get_episodic_manager()
        if manager is not None:
            manager.force_close("shutdown")
        self._pending_tools.clear()
        self._pending_context = None
        self._tool_rounds = 0

    def cleanup(self) -> None:
        if getattr(self, "_buddy_shutdown_done", False):
            return
        self._buddy_shutdown_done = True
        logger.info("LocalToolExecutor shutting down — running session cleanup")
        self.on_session_end()
