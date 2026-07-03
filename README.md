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

Update model paths in `start-llama-server-speech.bat` if your GGUF files live somewhere other than `D:\Llama\Models`.

## Usage

1. Start the LLM server (terminal 1):

   ```bat
   start-llama-server-speech.bat
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

Personalities define behavior and reference a voice by id. Shipped templates live in `personalities/` in the repo; at runtime Buddy uses your **user data directory** (see below). See `personalities/README.md`. Edit model name, TTS backend, and VAD settings in `start-speech-to-speech.ps1` as needed.

## User data directory

Memory, personalities, skills, and active-persona selection are stored outside the repo in a configurable data directory:

| Platform | Default location |
|----------|------------------|
| Windows | `%LOCALAPPDATA%\Buddy\` |
| macOS | `~/Library/Application Support/Buddy/` |
| Linux | `$XDG_DATA_HOME/buddy/` or `~/.local/share/buddy/` |

Set `BUDDY_DATA_DIR` before starting Buddy to use a custom path — for example a cloud-synced folder for automatic backup:

```powershell
# Windows PowerShell
$env:BUDDY_DATA_DIR = "D:\Google Drive\Buddy"
.\start-speech-to-speech.ps1
```

```bash
# macOS / Linux
export BUDDY_DATA_DIR="$HOME/Dropbox/Buddy"
python run_speech_to_speech.py ...
```

On first run, shipped personality templates (e.g. `buddy`) are copied into the data dir. Edit Buddy like any persona; delete `{BUDDY_DATA_DIR}/personalities/buddy/` and restart to reset from the factory template.

## Memory and local tools

Facts the assistant should remember across sessions are stored as markdown files under `{BUDDY_DATA_DIR}/memory/`. The `buddy_tools` package patches speech-to-speech to expose local tools the model can call during conversation:

- **Memory:** `list_memory`, `read_memory`, `update_memory`, `append_memory`, `write_memory`
- **Personalities:** `list_personalities`, `list_voices`, `switch_personality`, `switch_voice`, `create_personality`, `update_personality`, `delete_personality`
- **Vision:** `capture_camera` (webcam), `capture_screen` (display screenshot)

## Project layout

```
buddy/
├── run_speech_to_speech.py   # Entry point with buddy_tools patches applied
├── start-speech-to-speech.ps1
├── start-llama-server-speech.bat
├── voices/                   # Named voice clone pairs (audio.wav + ref_text.txt)
├── personalities/            # Shipped personality templates (seeded into data dir)
└── buddy_tools/              # Local tool integration (memory, camera, screen, …)
```

User data (memory, runtime personalities, `active.json`) lives in `BUDDY_DATA_DIR`, not in the repo.
