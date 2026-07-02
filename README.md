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

pip install -r requirements.txt
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

Voice cloning uses `cliff.wav` as the reference audio. Edit the system prompt, model name, and VAD settings in `start-speech-to-speech.ps1` as needed.

## Memory and local tools

Facts the assistant should remember across sessions are stored as markdown files in `memory/`. The `buddy_tools` package patches speech-to-speech to expose local tools the model can call during conversation:

- **Memory:** `list_memory`, `read_memory`, `update_memory`, `append_memory`, `write_memory`
- **Vision:** `capture_camera` (webcam), `capture_screen` (display screenshot)

## Project layout

```
buddy/
├── run_speech_to_speech.py   # Entry point with buddy_tools patches applied
├── start-speech-to-speech.ps1
├── start-llama-server-speech.bat
├── cliff.wav                 # Voice clone reference audio
├── memory/                   # Persistent markdown memory
└── buddy_tools/              # Local tool integration (memory, camera, screen, …)
```
