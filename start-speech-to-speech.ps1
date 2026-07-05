# Speech-to-speech local voice agent
# Requires start-llama-server-speech.bat running first (port 8080).
# That llama profile disables Gemma 4 thinking (preserve_thinking=false).
# Verify model id after starting llama:
#   curl http://127.0.0.1:8080/v1/models

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Wait-IfStartupFailed {
    if ($Host.Name -ne "ConsoleHost") {
        return
    }
    Write-Host ""
    Write-Host "Press Enter to close this window..." -ForegroundColor Yellow
    [void][System.Console]::ReadLine()
}

try {
    if (-not (Test-Path ".\.venv\Scripts\Activate.ps1")) {
        throw "Virtual environment not found. Run setup-venv.ps1 first."
    }

    .\.venv\Scripts\Activate.ps1

    $env:OPENAI_API_KEY = "not-needed"

    # TTS backend: "pocket" (voice clone from voices/audio.wav) or "qwen3" (ref audio + ref text).
    $ttsBackend = "pocket"

    # Model name must match what llama-server reports in /v1/models
    # (typically the .gguf filename without extension).
    # Must match the .gguf filename without extension (see /v1/models after starting llama).

    # $llamaModelName = "gemma-4-E4B-it-Q4_K_M"
    $llamaModelName = "gemma-4-12b-it-uncensored-Q4_K_M"

    # Optional: store memory and personalities in a cloud-synced folder (Dropbox, Google Drive, etc.)
    # $env:BUDDY_DATA_DIR = "D:\Dropbox\Buddy"

    # Active personality prompt + voice from user data dir and voices/.
    $startupJson = python -c "import json; from buddy_tools.infra.startup import resolve_startup_config; print(json.dumps(resolve_startup_config()))"
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to resolve active personality. Ensure personalities/buddy/ (template) and voices/cliff/ exist."
    }
    $startup = $startupJson | ConvertFrom-Json
    $voiceSystemPrompt = $startup.init_chat_prompt
    $voiceRefAudio = $startup.audio
    $voiceRefText = $startup.ref_text
    Write-Host "Buddy data dir: $($startup.data_dir)"
    Write-Host "Active personality: $($startup.personality_name) ($($startup.personality_id)), voice: $($startup.voice_id)"
    Write-Host "TTS backend: $ttsBackend"

    # VAD: wait for this much silence (ms) before treating your turn as finished.
    # Default is 64ms (very aggressive). Try 500-800 if it cuts you off mid-sentence.
    $vadMinSilenceMs = 600

    # Working-context management (issue #45):
    # - llama-server --ctx-size (16384 in start-llama-server-speech.bat) is the hard limit.
    # - BUDDY_CTX_SIZE should match ctx-size; output/safety reserves leave room for replies.
    # - chat_size: soft turn cap; compact_history: LLM summarization fallback after each turn.
    # - Buddy preflight (mask old tool outputs -> evict turns) runs before each LLM call.
    # Optional overrides:
    #   $env:BUDDY_CTX_SIZE = "16384"
    #   $env:BUDDY_CTX_OUTPUT_RESERVE = "1024"
    #   $env:BUDDY_CTX_SAFETY_MARGIN = "512"
    #   $env:BUDDY_CTX_MASK_KEEP_TURNS = "4"

    $pythonArgs = @(
        "run_speech_to_speech.py",
        "--mode", "local",
        "--stt", "parakeet-tdt",
        "--min_silence_ms", "$vadMinSilenceMs",
        "--llm_backend", "chat-completions",
        "--responses_api_base_url", "http://127.0.0.1:8080/v1",
        "--responses_api_api_key", "not-needed",
        "--chat_size", "20",
        "--compact_history",
        "--responses_api_stream",
        "--responses_api_disable_thinking",
        "--init_chat_prompt", $voiceSystemPrompt,
        "--model_name", $llamaModelName,
        "--enable_live_transcription"
    )

    if ($ttsBackend -eq "pocket") {
        $pythonArgs += @(
            "--tts", "pocket",
            "--pocket_tts_device", "cuda",
            "--pocket_tts_voice", $voiceRefAudio,
            "--pocket_tts_max_tokens", "120"
        )
    } elseif ($ttsBackend -eq "qwen3") {
        $pythonArgs += @(
            "--tts", "qwen3",
            "--qwen3_tts_backend", "torch",
            "--qwen3_tts_model_name", "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
            "--qwen3_tts_ref_audio", $voiceRefAudio,
            "--qwen3_tts_ref_text", $voiceRefText
        )
    } else {
        throw "Unknown TTS backend: $ttsBackend (use 'pocket' or 'qwen3')"
    }

    & python @pythonArgs
    if ($LASTEXITCODE -ne 0) {
        throw "run_speech_to_speech.py exited with code $LASTEXITCODE"
    }
} catch {
    Write-Host ""
    Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
    if ($_.ScriptStackTrace) {
        Write-Host $_.ScriptStackTrace -ForegroundColor DarkRed
    }
    Wait-IfStartupFailed
    exit 1
}
