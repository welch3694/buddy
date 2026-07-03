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

# Active personality prompt + voice from personalities/ and voices/.
$startupJson = python -c "import json; from buddy_tools.startup import resolve_startup_config; print(json.dumps(resolve_startup_config()))"
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to resolve active personality. Ensure personalities/buddy/ and voices/cliff/ exist."
}
$startup = $startupJson | ConvertFrom-Json
$voiceSystemPrompt = $startup.init_chat_prompt
$voiceRefAudio = $startup.audio
$voiceRefText = $startup.ref_text
Write-Host "Active personality: $($startup.personality_name) ($($startup.personality_id)), voice: $($startup.voice_id)"

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
