"""Execute local tool calls and trigger LLM follow-up."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from pathlib import Path
from queue import Queue
from typing import Any

from openai.types.realtime import (
    RealtimeConversationItemFunctionCall,
    RealtimeConversationItemFunctionCallOutput,
    RealtimeConversationItemUserMessage,
)
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
from buddy_tools.core.tool_logging import is_tool_error, log_tool_bypass, safe_tool_context
from buddy_tools.core.tool_receipts import (
    ToolReceipt,
    claims_without_receipt,
    find_action_claims,
    has_matching_receipt,
    make_tool_receipt,
)
from buddy_tools.timers import cancel_all_timers
from buddy_tools.episodic import (
    EpisodicTurnRecord,
    get_episodic_manager,
    reconfigure_episodic_persona,
)
from buddy_tools.episodic.turns import truncate_tool_output
from buddy_tools.channels.turn_context import get_turn
from buddy_tools.channels.last_capture import store_last_capture
from buddy_tools.voice.session import apply_voice
from buddy_tools.themes.session import apply_theme
from buddy_tools.voice.action_intents import (
    clear_action_intent,
    peek_action_intent,
    pop_action_intent,
)

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 5
MAX_REQUIRED_TOOL_RETRIES = 2
REQUIRED_TOOL_NUDGE_PREFIX = (
    "[Required tool — internal nudge, not user speech]: "
    "Call {tool_name} now; do not claim success without a tool result."
)
_SKIPPED_TOOL_OUTPUT = (
    "Error: tool round limit reached; this tool was not executed. "
    "Summarize what you have and tell the user you could not finish looking up memory."
)
CLAIM_TTS_FALLBACK = "I didn't actually run that — say it again and I'll try."


def _emit_tool_call_for_receipt(
    receipt: ToolReceipt,
    *,
    source: str,
    turn_id: str | None,
) -> None:
    from buddy_tools.companion.events import format_tool_call_summary
    from buddy_tools.companion.publisher import emit_tool_call

    emit_tool_call(
        tool=receipt.tool,
        status=receipt.status,
        summary=format_tool_call_summary(
            receipt.tool,
            receipt.status,
            receipt.args_summary,
        ),
        source=source,
        turn_id=turn_id,
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
        self._required_tool_retries = 0
        self._turn_text_buffer: list[str] = []
        self._turn_receipts: list[ToolReceipt] = []

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

    def _inject_skipped_tool_errors_and_follow_up(self, *, source: str = "llm") -> bool:
        if not self._pending_tools or self._pending_context is None or self.text_prompt_queue is None:
            return False

        runtime_config: RuntimeConfig = self._pending_context.runtime_config
        chat = runtime_config.chat
        pending = list(self._pending_tools)
        pending_names = [tool.name for tool in pending]
        turn_id = self._pending_context.turn_id
        logger.warning(
            "Max tool rounds (%d) reached; skipping %d tools: %s",
            MAX_TOOL_ROUNDS,
            len(pending_names),
            pending_names,
        )

        for tool in pending:
            raw_args = tool.arguments if isinstance(tool.arguments, str) else None
            receipt = make_tool_receipt(
                tool.name,
                _parse_tool_arguments(raw_args),
                status="skipped",
            )
            self._turn_receipts.append(receipt)
            _emit_tool_call_for_receipt(receipt, source=source, turn_id=turn_id)
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

    def _execute_pending_tools(self, *, source: str = "llm") -> bool:
        if not self._pending_tools or self._pending_context is None or self.text_prompt_queue is None:
            return False

        if self._tool_rounds >= MAX_TOOL_ROUNDS:
            return self._inject_skipped_tool_errors_and_follow_up(source=source)

        runtime_config: RuntimeConfig = self._pending_context.runtime_config
        chat = runtime_config.chat
        turn_id = self._pending_context.turn_id

        for tool in self._pending_tools:
            result = execute_tool(
                self.memory_root,
                tool.name,
                tool.arguments,
                persona_namespace=self.persona_namespace,
                turn_id=turn_id,
                turn_revision=self._pending_context.turn_revision,
            )
            raw_args = tool.arguments if isinstance(tool.arguments, str) else None
            receipt = make_tool_receipt(
                tool.name,
                _parse_tool_arguments(raw_args),
                result=result,
            )
            self._turn_receipts.append(receipt)
            _emit_tool_call_for_receipt(receipt, source=source, turn_id=turn_id)
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

            if result.theme_switch_id:
                try:
                    theme_pack = apply_theme(result.theme_switch_id)
                    result = ToolExecutionResult(output=f"Now using the {theme_pack.name} theme.")
                except (FileNotFoundError, ValueError, OSError) as exc:
                    logger.exception("Tool %s: theme switch failed", tool.name)
                    result = ToolExecutionResult(output=f"Error: could not switch theme: {exc}")

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
                store_last_capture(result.image_data_uri)
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

    def _coerce_pending_tools_from_stash(self, turn_id: str | None) -> None:
        """Replace LLM tool args with stashed intent args when the tool name matches."""
        intent = peek_action_intent(turn_id)
        if intent is None or not self._pending_tools:
            return
        desired = json.dumps(intent.arguments)
        coerced: list[ResponseFunctionToolCall] = []
        for tool in self._pending_tools:
            if tool.name != intent.tool_name:
                coerced.append(tool)
                continue
            raw = tool.arguments if isinstance(tool.arguments, str) else None
            if raw == desired:
                coerced.append(tool)
                continue
            logger.warning(
                "Coercing %s arguments to stashed intent (was %s, turn=%s)",
                intent.tool_name,
                raw,
                turn_id,
            )
            coerced.append(tool.model_copy(update={"arguments": desired}))
        self._pending_tools = coerced

    def _required_tool_unmet(self, turn_id: str | None) -> bool:
        """True when a stashed action intent still lacks a successful receipt."""
        intent = peek_action_intent(turn_id)
        if intent is None:
            return False
        return not has_matching_receipt(self._turn_receipts, intent.tool_name)

    def _clear_action_intent_if_matched(self, turn_id: str | None) -> None:
        """Clear stashed intent only when a matching tool receipt exists (or no stash)."""
        intent = peek_action_intent(turn_id)
        if intent is None:
            return
        if has_matching_receipt(self._turn_receipts, intent.tool_name):
            clear_action_intent(turn_id)

    def _retry_required_tool(self, turn_id: str | None) -> bool:
        """Nudge + re-queue with forced tool_choice when a required tool has no receipt."""
        if self._pending_tools or self._pending_context is None or self.text_prompt_queue is None:
            return False
        intent = peek_action_intent(turn_id)
        if intent is None:
            return False
        if has_matching_receipt(self._turn_receipts, intent.tool_name):
            return False
        if self._required_tool_retries >= MAX_REQUIRED_TOOL_RETRIES:
            return False

        from openai.types.realtime.realtime_response_create_params import RealtimeResponseCreateParams
        from openai.types.responses.tool_choice_function import ToolChoiceFunction

        nudge_text = REQUIRED_TOOL_NUDGE_PREFIX.format(tool_name=intent.tool_name)
        chat = self._pending_context.runtime_config.chat
        try:
            chat.add_item(
                RealtimeConversationItemUserMessage(
                    type="message",
                    role="user",
                    content=[UserContent(type="input_text", text=nudge_text)],
                )
            )
        except ChatItemError as exc:
            logger.error(
                "Could not inject required-tool nudge for %s: %s",
                intent.tool_name,
                exc,
            )
            return False

        ctx = self._pending_context
        self.text_prompt_queue.put(
            GenerateResponseRequest(
                runtime_config=ctx.runtime_config,
                language_code=ctx.language_code,
                turn_id=ctx.turn_id,
                turn_revision=ctx.turn_revision,
                speech_stopped_at_s=ctx.speech_stopped_at_s,
                response=RealtimeResponseCreateParams(
                    tool_choice=ToolChoiceFunction(type="function", name=intent.tool_name),
                ),
            )
        )
        self._required_tool_retries += 1
        self._turn_text_buffer.clear()
        logger.warning(
            "Required tool %s missing receipt; nudge retry %d/%d (turn=%s)",
            intent.tool_name,
            self._required_tool_retries,
            MAX_REQUIRED_TOOL_RETRIES,
            turn_id,
        )
        return True

    def _silent_execute_stashed_intent(self, turn_id: str | None) -> bool:
        """If forced tool_choice was ignored, execute the stashed ActionIntent."""
        if self._pending_tools or self._pending_context is None or self.text_prompt_queue is None:
            return False
        intent = peek_action_intent(turn_id)
        if intent is None:
            return False
        if has_matching_receipt(self._turn_receipts, intent.tool_name):
            clear_action_intent(turn_id)
            return False
        intent = pop_action_intent(turn_id)
        if intent is None:
            return False

        call_id = f"call_silent_{turn_id or 'unknown'}"
        arguments_json = json.dumps(intent.arguments)
        chat = self._pending_context.runtime_config.chat
        try:
            chat.add_item(
                RealtimeConversationItemFunctionCall(
                    type="function_call",
                    name=intent.tool_name,
                    arguments=arguments_json,
                    call_id=call_id,
                )
            )
        except ChatItemError as exc:
            logger.error(
                "Could not record silent function_call for %s: %s",
                intent.tool_name,
                exc,
            )
            return False

        logger.warning(
            "Forced tool_choice ignored; silently executing %s (turn=%s)",
            intent.tool_name,
            turn_id,
        )
        self._pending_tools = [
            ResponseFunctionToolCall(
                type="function_call",
                name=intent.tool_name,
                arguments=arguments_json,
                call_id=call_id,
                id=f"fc_{call_id}",
            )
        ]
        return self._execute_pending_tools(source="silent")

    def _claim_tts_fallback_chunk(self) -> LLMResponseChunk | None:
        """Build a spoken fallback chunk from the last remembered turn context."""
        ctx = self._pending_context
        if ctx is None:
            return None
        return LLMResponseChunk(
            text=CLAIM_TTS_FALLBACK,
            language_code=ctx.language_code,
            runtime_config=ctx.runtime_config,
            response=ctx.response,
            turn_id=ctx.turn_id,
            turn_revision=ctx.turn_revision,
            speech_stopped_at_s=ctx.speech_stopped_at_s,
        )

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
            full_text = "".join(self._turn_text_buffer)
            # Hold speech while a routed required tool has not succeeded yet,
            # and while claim heuristics fire with no receipts.
            if not self._pending_tools and (
                self._required_tool_unmet(lm_output.turn_id)
                or claims_without_receipt(full_text, self._turn_receipts)
            ):
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

            if self._pending_tools:
                self._coerce_pending_tools_from_stash(lm_output.turn_id)
                if self._execute_pending_tools():
                    self._clear_action_intent_if_matched(lm_output.turn_id)
                    self._turn_text_buffer.clear()
                    return

            if self._retry_required_tool(lm_output.turn_id):
                return

            if self._silent_execute_stashed_intent(lm_output.turn_id):
                self._turn_text_buffer.clear()
                return

            full_text = "".join(self._turn_text_buffer).strip()
            self._turn_text_buffer.clear()
            spoken_text = full_text
            fallback_chunk: LLMResponseChunk | None = None
            if claims_without_receipt(full_text, self._turn_receipts):
                claims = find_action_claims(full_text)
                preview = full_text if len(full_text) <= 80 else full_text[:80] + "..."
                log_tool_bypass(
                    "assistant claimed action without tool receipt",
                    context={
                        "turn_id": lm_output.turn_id,
                        "claims": claims,
                        "receipt_count": len(self._turn_receipts),
                        "text_preview": preview,
                    },
                )
                fallback_chunk = self._claim_tts_fallback_chunk()
                spoken_text = CLAIM_TTS_FALLBACK if fallback_chunk is not None else ""
            handle_pulse_end_of_response()
            record_assistant_speech_for_active_pulse(spoken_text)
            _log_episodic_assistant_turn(lm_output.turn_id, spoken_text)

            self._tool_rounds = 0
            self._required_tool_retries = 0
            self._pending_context = None
            self._turn_receipts.clear()
            if fallback_chunk is not None:
                from buddy_tools.companion.publisher import emit_assistant_text

                emit_assistant_text(
                    fallback_chunk.text,
                    turn_id=fallback_chunk.turn_id,
                    turn_revision=fallback_chunk.turn_revision,
                )
                yield fallback_chunk
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
        self._required_tool_retries = 0
        self._turn_receipts.clear()

    def cleanup(self) -> None:
        if getattr(self, "_buddy_shutdown_done", False):
            return
        self._buddy_shutdown_done = True
        logger.info("LocalToolExecutor shutting down — running session cleanup")
        self.on_session_end()
