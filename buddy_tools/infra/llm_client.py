"""Thin OpenAI-compatible chat completion client for off-hot-path LLM calls."""

from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeAlias

logger = logging.getLogger(__name__)

DEFAULT_LLM_BASE_URL = "http://127.0.0.1:8080/v1"
DEFAULT_LLM_MODEL_DIR = r"D:\Llama\Models"
DEFAULT_LLM_MMPROJ = "mmproj-gemma-4-12B-it-bf16.gguf"
DEFAULT_LLM_SERVER_EXE = r"D:\Llama\llama-server.exe"

_ENV_LLM_BASE_URL = "BUDDY_LLM_BASE_URL"
_ENV_LLM_MODEL_NAME = "BUDDY_LLM_MODEL_NAME"
_ENV_LLM_MODEL_DIR = "BUDDY_LLM_MODEL_DIR"
_ENV_LLM_MMPROJ = "BUDDY_LLM_MMPROJ"
_ENV_LLM_SERVER_EXE = "BUDDY_LLM_SERVER_EXE"

LlmFn: TypeAlias = Callable[[str, str], str]


def get_llm_base_url() -> str:
    raw = os.environ.get(_ENV_LLM_BASE_URL, "").strip()
    return raw or DEFAULT_LLM_BASE_URL


def get_llm_model_name() -> str:
    return os.environ.get(_ENV_LLM_MODEL_NAME, "").strip()


def require_llm_model_name() -> str:
    """Return the configured model id or raise with setup guidance."""
    model = get_llm_model_name()
    if model:
        return model
    raise RuntimeError(
        f"{_ENV_LLM_MODEL_NAME} is not set. Add it to .env in the repo root "
        f"(see .env.example). It must match the loaded GGUF filename without "
        f"the .gguf extension — verify with: curl {get_llm_base_url()}/models"
    )


def get_llm_model_dir() -> Path:
    raw = os.environ.get(_ENV_LLM_MODEL_DIR, "").strip()
    return Path(raw or DEFAULT_LLM_MODEL_DIR)


def get_llm_mmproj_filename() -> str:
    raw = os.environ.get(_ENV_LLM_MMPROJ, "").strip()
    return raw or DEFAULT_LLM_MMPROJ


def get_llm_server_exe() -> Path:
    raw = os.environ.get(_ENV_LLM_SERVER_EXE, "").strip()
    return Path(raw or DEFAULT_LLM_SERVER_EXE)


def resolve_llm_gguf_path() -> Path:
    return get_llm_model_dir() / f"{require_llm_model_name()}.gguf"


def resolve_llm_mmproj_path() -> Path:
    return get_llm_model_dir() / get_llm_mmproj_filename()


def resolve_llm_server_config() -> dict[str, str]:
    """Paths and ids for launching llama-server and OpenAI-compatible clients."""
    return {
        "model_name": require_llm_model_name(),
        "model_dir": str(get_llm_model_dir()),
        "model_gguf": str(resolve_llm_gguf_path()),
        "mmproj": str(resolve_llm_mmproj_path()),
        "server_exe": str(get_llm_server_exe()),
        "base_url": get_llm_base_url(),
    }


def complete_chat(
    system: str,
    user: str,
    *,
    temperature: float = 0.2,
    llm_fn: LlmFn | None = None,
) -> str:
    """Run a one-shot chat completion. Uses llm_fn when provided (tests)."""
    if llm_fn is not None:
        return llm_fn(system, user)

    model = require_llm_model_name()

    from openai import OpenAI

    client = OpenAI(base_url=get_llm_base_url(), api_key="not-needed")
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temperature,
    )
    choice = response.choices[0].message.content
    if not choice:
        raise RuntimeError("LLM returned empty completion")
    return choice.strip()


def _cli_main(argv: list[str] | None = None) -> int:
    """Print resolved LLM config as JSON (for PowerShell launch scripts)."""
    from buddy_tools.infra.env import load_env_file

    load_env_file()
    args = argv if argv is not None else sys.argv[1:]
    if not args or args[0] == "config":
        payload: dict[str, Any] = resolve_llm_server_config()
        print(json.dumps(payload))
        return 0
    if args[0] == "model-name":
        print(require_llm_model_name())
        return 0
    print("Usage: python -m buddy_tools.infra.llm_client [config|model-name]", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(_cli_main())
