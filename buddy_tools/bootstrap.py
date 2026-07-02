"""Wire local tools into the speech-to-speech runtime config and pipeline."""

from __future__ import annotations

from pathlib import Path
from queue import Queue
from threading import Event
from typing import Any

from speech_to_speech.api.openai_realtime.runtime_config import RuntimeConfig

from buddy_tools.registry import ALL_TOOL_DEFINITIONS, build_tool_instructions, load_memory_summary
from buddy_tools.startup import build_init_instructions

_MEMORY_DIR = Path(__file__).resolve().parent.parent / "memory"


def get_memory_dir() -> Path:
    return _MEMORY_DIR


def set_memory_dir(path: Path) -> None:
    global _MEMORY_DIR
    _MEMORY_DIR = path.resolve()


def configure_runtime_tools(runtime_config: RuntimeConfig | None, memory_dir: Path | None = None) -> None:
    """Register local tools and preload memory into session instructions."""
    if runtime_config is None:
        return

    root = (memory_dir or _MEMORY_DIR).resolve()
    root.mkdir(parents=True, exist_ok=True)

    summary = load_memory_summary(root)
    base = build_init_instructions()
    runtime_config.session.instructions = build_tool_instructions(base, summary)
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
) -> list[Any]:
    """Insert LocalToolExecutor between the LLM handler and LMOutputProcessor."""
    from buddy_tools.executor import LocalToolExecutor

    runtime_config = transcription_notifier_setup.get("runtime_config")
    memory_dir = get_memory_dir()
    configure_runtime_tools(runtime_config, memory_dir)

    lm_bridge: Queue[Any] = Queue()
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
                    queue_out=lm_response_queue,
                    setup_kwargs={
                        "text_prompt_queue": text_prompt_queue,
                        "memory_dir": memory_dir,
                        "speculative_turns": speculative_turns,
                    },
                )
            )
            continue
        new_handlers.append(handler)

    return new_handlers
