"""Wire local tools into the speech-to-speech runtime config and pipeline."""

from __future__ import annotations

from pathlib import Path
from queue import Queue
from threading import Event
from typing import Any

from speech_to_speech.api.openai_realtime.runtime_config import RuntimeConfig

from buddy_tools.memory import migrate_legacy_memory
from buddy_tools.personality import get_active_personality
from buddy_tools.registry import ALL_TOOL_DEFINITIONS, build_tool_instructions, load_memory_summary
from buddy_tools.startup import build_init_instructions
from buddy_tools.timers import configure_timers
from buddy_tools.voice_session import apply_startup_voice, register_pipeline_handlers

_MEMORY_ROOT = Path(__file__).resolve().parent.parent / "memory"


def get_memory_root() -> Path:
    return _MEMORY_ROOT


def set_memory_root(path: Path) -> None:
    global _MEMORY_ROOT
    _MEMORY_ROOT = path.resolve()


def get_memory_dir() -> Path:
    """Return the memory root directory (legacy name)."""
    return get_memory_root()


def set_memory_dir(path: Path) -> None:
    set_memory_root(path)


def configure_runtime_tools(runtime_config: RuntimeConfig | None, memory_root: Path | None = None) -> None:
    """Register local tools and preload memory into session instructions."""
    if runtime_config is None:
        return

    root = (memory_root or _MEMORY_ROOT).resolve()
    root.mkdir(parents=True, exist_ok=True)
    migrate_legacy_memory(root)
    profile = get_active_personality()
    summary = load_memory_summary(root, profile.memory_namespace)
    base = build_init_instructions()
    runtime_config.session.instructions = build_tool_instructions(
        base,
        summary,
        memory_root=root,
        persona_namespace=profile.memory_namespace,
        personality_id=profile.id,
    )
    runtime_config.session.tools = list(ALL_TOOL_DEFINITIONS)
    runtime_config.session.tool_choice = "auto"


def insert_local_tool_executor(
    handlers: list[Any],
    *,
    stop_event: Event,
    text_prompt_queue: Queue[Any],
    lm_response_queue: Queue[Any],
    transcription_notifier_setup: dict[str, Any],
    speculative_turns: Any | None,
    should_listen: Event | None = None,
) -> list[Any]:
    """Insert LocalToolExecutor and channel reply routing before LMOutputProcessor."""
    from buddy_tools.channels.reply_router import ChannelReplyRouter
    from buddy_tools.channels.telegram import create_and_start_telegram_bridge
    from buddy_tools.executor import LocalToolExecutor

    runtime_config = transcription_notifier_setup.get("runtime_config")
    memory_root = get_memory_root()
    profile = get_active_personality()
    configure_runtime_tools(runtime_config, memory_root)
    configure_timers(
        text_prompt_queue=text_prompt_queue,
        runtime_config=runtime_config,
        should_listen=should_listen,
    )

    telegram_bridge = create_and_start_telegram_bridge(
        runtime_config=runtime_config,
        text_prompt_queue=text_prompt_queue,
        stop_event=stop_event,
    )
    send_telegram_reply = None
    if telegram_bridge is not None:
        send_telegram_reply = telegram_bridge.send_reply

    lm_bridge: Queue[Any] = Queue()
    channel_bridge: Queue[Any] = Queue()
    new_handlers: list[Any] = []

    for handler in handlers:
        class_name = handler.__class__.__name__
        if class_name.endswith("ApiModelHandler") or class_name in {
            "LanguageModelHandler",
            "VisionLanguageModelHandler",
            "ResponsesApiModelHandler",
        }:
            handler.queue_out = lm_bridge
            new_handlers.append(handler)
            new_handlers.append(
                LocalToolExecutor(
                    stop_event,
                    queue_in=lm_bridge,
                    queue_out=channel_bridge,
                    setup_kwargs={
                        "text_prompt_queue": text_prompt_queue,
                        "memory_root": memory_root,
                        "persona_namespace": profile.memory_namespace,
                        "speculative_turns": speculative_turns,
                    },
                )
            )
            new_handlers.append(
                ChannelReplyRouter(
                    stop_event,
                    queue_in=channel_bridge,
                    queue_out=lm_response_queue,
                    setup_kwargs={
                        "send_telegram_reply": send_telegram_reply,
                        "speculative_turns": speculative_turns,
                    },
                )
            )
            continue
        new_handlers.append(handler)

    register_pipeline_handlers(new_handlers)
    apply_startup_voice(runtime_config=runtime_config)
    return new_handlers
