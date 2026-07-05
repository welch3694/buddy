# Buddy

Local voice assistant built on [Hugging Face speech-to-speech](https://github.com/huggingface/speech-to-speech), with persistent markdown memory the model can read and update during conversation.

## Prerequisites

- Python 3.12+
- [llama.cpp](https://github.com/ggerganov/llama.cpp) `llama-server` with a Gemma 4 GGUF model
- A CUDA-capable GPU (recommended for STT/TTS)
- Microphone and speakers

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Install PyTorch for your CUDA version — see https://pytorch.org
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128

# Do not use plain pip install -r requirements.txt for everything — speech-to-speech
# pulls faster-qwen3-tts[ggml], which fails on Windows (no qwentts-cpp-python wheels).
.\setup-venv.ps1
```

Set `BUDDY_LLM_MODEL_DIR` and related paths in `.env` if your GGUF files live somewhere other than `D:\Llama\Models`.

## Usage

1. Start the LLM server (terminal 1):

   ```powershell
   .\start-llama-server-speech.ps1
   ```

2. Confirm the model name matches what the server reports:

   ```powershell
   curl http://127.0.0.1:8080/v1/models
   ```

3. Start the voice agent (terminal 2):

   ```powershell
   .\start-speech-to-speech.ps1
   ```

Voice cloning uses named voices in `voices/` (default: `cliff`). Each voice folder contains `audio.wav` and `ref_text.txt`. See `voices/README.md` to add voices manually.

TTS defaults to **Pocket TTS** with voice cloning from `voices/cliff/audio.wav` (requires Hugging Face login and Kyutai terms). Set `$ttsBackend = "qwen3"` in `start-speech-to-speech.ps1` to use Qwen3 instead (occasional timbre drift; see issue #12).

Personalities define behavior and reference a voice by id. Shipped templates live in `personalities/` in the repo; at runtime Buddy uses your **user data directory** (see below). See `personalities/README.md`. Set `BUDDY_LLM_MODEL_NAME` in `.env` (see `.env.example`); edit TTS backend and VAD settings in `start-speech-to-speech.ps1` as needed.

## Telegram (mobile chat)

While Buddy is running, you can send text and photos from your phone via Telegram. Messages share the same conversation context as voice — for example, you can ask about an outfit by voice, send a photo on Telegram, then follow up by voice with the camera tool.

The Telegram bot is **only active while Buddy is running**; when you stop the voice agent, the chat channel goes offline.

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy the bot token.
2. Find your Telegram chat ID (message [@userinfobot](https://t.me/userinfobot) or send a message to your bot and inspect updates).
3. Configure credentials — easiest is a gitignored `.env` file in the repo root:

   ```powershell
   copy .env.example .env
   # Edit .env and set TELEGRAM_BOT_TOKEN and TELEGRAM_ALLOWED_CHAT_IDS
   .\start-speech-to-speech.ps1
   ```

   Buddy loads `.env` automatically at startup (existing shell env vars take precedence). You can also set them inline:

   ```powershell
   $env:TELEGRAM_BOT_TOKEN = "123456:ABC..."
   $env:TELEGRAM_ALLOWED_CHAT_IDS = "123456789"
   .\start-speech-to-speech.ps1
   ```

   Alternatively, save allowed chat IDs (not the token) in `{BUDDY_DATA_DIR}/telegram.json`:

   ```json
   { "allowed_chat_ids": [123456789] }
   ```

   When both are set, `TELEGRAM_ALLOWED_CHAT_IDS` overrides the file.

Replies to Telegram messages are sent as text on Telegram; voice turns still use TTS. Only allowlisted chat IDs are accepted.

## User data directory

Memory, personalities, skills, and active-persona selection are stored outside the repo in a configurable data directory:

| Platform | Default location |
|----------|------------------|
| Windows | `%LOCALAPPDATA%\Buddy\` |
| macOS | `~/Library/Application Support/Buddy/` |
| Linux | `$XDG_DATA_HOME/buddy/` or `~/.local/share/buddy/` |

Set `BUDDY_DATA_DIR` to use a custom path — for example a cloud-synced folder for automatic backup. Add it to `.env` (see `.env.example`) or set it in the shell before starting:

```powershell
# Windows PowerShell — or put BUDDY_DATA_DIR=... in .env
$env:BUDDY_DATA_DIR = "D:\Google Drive\Buddy"
.\start-speech-to-speech.ps1
```

```bash
# macOS / Linux — or put BUDDY_DATA_DIR=... in .env
export BUDDY_DATA_DIR="$HOME/Dropbox/Buddy"
python run_speech_to_speech.py ...
```

On first run, shipped personality templates (e.g. `buddy`) are copied into the data dir. Edit Buddy like any persona; delete `{BUDDY_DATA_DIR}/personalities/buddy/` and restart to reset from the factory template.

## Memory and local tools

Facts the assistant should remember across sessions are stored as markdown files under `{BUDDY_DATA_DIR}/memory/`. Global notes (`memory/global/`) are shared across personas; persona notes (`memory/{persona}/`) are scoped to the active personality. The built-in **remember** skill (`skills/remember/`) guides a voice-friendly flow when the user says "remember that…" — confirm the fact, choose everyone vs between us, then save via memory tools.

The `buddy_tools` package patches speech-to-speech to expose local tools the model can call during conversation:

- **Memory:** `list_memory`, `read_memory`, `update_memory`, `append_memory`, `write_memory`
- **Personalities:** `list_personalities`, `list_voices`, `switch_personality`, `switch_voice`, `create_personality`, `update_personality`, `delete_personality`
- **Vision:** `capture_camera` (webcam), `capture_screen` (display screenshot)

## Working-context management

Buddy keeps the live LLM chat buffer within a token budget so long sessions, tool round-trips, and vision captures do not exceed llama-server context and crash generation. This is separate from long-term markdown memory on disk.

Compaction order:

1. **Observation masking** — old tool outputs replaced with compact placeholders (no extra LLM call)
2. **Turn eviction** — oldest user turns dropped when still over budget
3. **LLM summarization** — `--compact_history` in `start-speech-to-speech.ps1` summarizes older turns in the background after each successful generation

If llama-server still rejects a request for context length, Buddy trims aggressively and speaks a brief apology instead of failing silently.

| Setting | Default | Purpose |
|---------|---------|---------|
| `BUDDY_CTX_SIZE` | `16384` | Must match llama-server `--ctx-size` |
| `BUDDY_CTX_OUTPUT_RESERVE` | `1024` | Tokens reserved for the model reply |
| `BUDDY_CTX_SAFETY_MARGIN` | `512` | Extra headroom for tool schemas and estimation error |
| `BUDDY_CTX_MASK_KEEP_TURNS` | `4` | Recent turns whose tool outputs stay at full detail |

Launcher flags: `--chat_size 20` (soft turn cap), `--compact_history` (summarization fallback). See comments in `start-speech-to-speech.ps1`.

## Project layout

```
buddy/
├── run_speech_to_speech.py   # Entry point with buddy_tools patches applied
├── start-speech-to-speech.ps1
├── start-llama-server-speech.ps1
├── voices/                   # Named voice clone pairs (audio.wav + ref_text.txt)
├── personalities/            # Shipped personality templates (seeded into data dir)
├── skills/                   # Global built-in skills (read-only at runtime)
└── buddy_tools/              # Local tool integration (memory, camera, screen, …)
```

User data (memory, runtime personalities, `active.json`) lives in `BUDDY_DATA_DIR`, not in the repo.
