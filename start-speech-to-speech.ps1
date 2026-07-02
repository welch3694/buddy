# Speech-to-speech local voice agent
# Requires start-llama-server-speech.bat running first (port 8080).
# That llama profile disables Gemma 4 thinking (preserve_thinking=false).
# Verify model id after starting llama:
#   curl http://127.0.0.1:8080/v1/models

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".\.venv\Scripts\Activate.ps1")) {
    Write-Error "Virtual environment not found. Run setup first."
}

.\.venv\Scripts\Activate.ps1

$env:OPENAI_API_KEY = "not-needed"

# Model name must match what llama-server reports in /v1/models
# (typically the .gguf filename without extension).
# Must match the .gguf filename without extension (see /v1/models after starting llama).

# $llamaModelName = "gemma-4-E4B-it-Q4_K_M"
$llamaModelName = "gemma-4-12b-it-uncensored-Q4_K_M"

$personalityPath = Join-Path $PSScriptRoot "personality.md"
if (-not (Test-Path $personalityPath)) {
    Write-Error "personality.md not found at $personalityPath"
}

# Fixed instructions appended after personality.md (not editable via personality file).
$fixedInstructions = @"
Reply directly in natural spoken language only. 
Never explain your reasoning, planning, or what the user asked for. 
Be warm and conversational, not formal or robotic. 
Keep answers concise unless the user asks for more detail. 
Do not mention tools, files, memory, or how you work unless the user explicitly asks. 
"@.Trim()

$voiceSystemPrompt = "$((Get-Content $personalityPath -Raw).Trim())`n`n$fixedInstructions"

# Voice clone reference from voices/{id}/ (audio.wav + ref_text.txt).
$voiceInfoJson = python -c "import json; from buddy_tools.voices import resolve_voice, DEFAULT_VOICE_ID; audio, text = resolve_voice(DEFAULT_VOICE_ID); print(json.dumps({'audio': str(audio), 'ref_text': text}))"
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to resolve default voice. Ensure voices/cliff/audio.wav and voices/cliff/ref_text.txt exist."
}
$voiceInfo = $voiceInfoJson | ConvertFrom-Json
$voiceRefAudio = $voiceInfo.audio
$voiceRefText = $voiceInfo.ref_text

# VAD: wait for this much silence (ms) before treating your turn as finished.
# Default is 64ms (very aggressive). Try 500-800 if it cuts you off mid-sentence.
$vadMinSilenceMs = 600

python run_speech_to_speech.py `
    --mode local `
    --stt parakeet-tdt `
    --min_silence_ms $vadMinSilenceMs `
    --llm_backend chat-completions `
    --responses_api_base_url "http://127.0.0.1:8080/v1" `
    --responses_api_api_key "not-needed" `
    --responses_api_stream `
    --responses_api_disable_thinking `
    --init_chat_prompt "$voiceSystemPrompt" `
    --model_name $llamaModelName `
    --tts qwen3 `
    --qwen3_tts_backend torch `
    --qwen3_tts_model_name "Qwen/Qwen3-TTS-12Hz-1.7B-Base" `
    --qwen3_tts_ref_audio $voiceRefAudio `
    --qwen3_tts_ref_text $voiceRefText `
    --enable_live_transcription
