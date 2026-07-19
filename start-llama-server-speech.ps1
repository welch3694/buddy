# Gemma 4 llama-server profile tuned for speech-to-speech voice chat.
# Model name and paths come from .env (BUDDY_LLM_*). Start before start-speech-to-speech.ps1.

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

    $configJson = python -m buddy_tools.infra.llm_client config
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to resolve LLM config. Set BUDDY_LLM_MODEL_NAME in .env (see .env.example)."
    }
    $llm = $configJson | ConvertFrom-Json

    if (-not (Test-Path $llm.server_exe)) {
        throw "llama-server not found at $($llm.server_exe). Set BUDDY_LLM_SERVER_EXE in .env if needed."
    }
    if (-not (Test-Path $llm.model_gguf)) {
        throw "Model GGUF not found at $($llm.model_gguf). Check BUDDY_LLM_MODEL_NAME and BUDDY_LLM_MODEL_DIR in .env."
    }
    if (-not (Test-Path $llm.mmproj)) {
        throw "mmproj not found at $($llm.mmproj). Set BUDDY_LLM_MMPROJ in .env if needed."
    }

    Write-Host "LLM model: $($llm.model_name)"
    Write-Host "GGUF: $($llm.model_gguf)"

    # Disable Gemma 4 thinking tokens for voice chat.
    # Pass JSON via env — Windows PowerShell 5.1 (powershell.exe) strips " from
    # native CLI args, so --chat-template-kwargs '{"preserve_thinking":false}'
    # arrives as {preserve_thinking:false} and llama-server fails with:
    #   parse error ... last read: '{p'; expected string literal
    # That error is unrelated to personality prompt.md edits; restarting after
    # an edit just re-hits this quoting bug when start-buddy.ps1 launches 5.1.
    $chatTemplateKwargs = '{"preserve_thinking":false}'
    try {
        $null = $chatTemplateKwargs | ConvertFrom-Json
    } catch {
        throw "Internal chat-template-kwargs is not valid JSON: $chatTemplateKwargs"
    }
    $env:LLAMA_ARG_CHAT_TEMPLATE_KWARGS = $chatTemplateKwargs
    Write-Host "Chat template kwargs: $chatTemplateKwargs"

    # Keep --ctx-size in sync with BUDDY_CTX_SIZE (default 16384) in start-speech-to-speech.ps1
    & $llm.server_exe `
        -m $llm.model_gguf `
        --mmproj $llm.mmproj `
        --n-gpu-layers 99 `
        --ctx-size 16384 `
        --reasoning off `
        --temperature 0.3 `
        --repeat-penalty 1.1 `
        --min-p 0.1 `
        --host 0.0.0.0 --port 8080 `
        --flash-attn on `
        --log-colors on

    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "llama-server exited with code $LASTEXITCODE." -ForegroundColor Red
        if ($env:LLAMA_ARG_CHAT_TEMPLATE_KWARGS) {
            Write-Host "LLAMA_ARG_CHAT_TEMPLATE_KWARGS was: $($env:LLAMA_ARG_CHAT_TEMPLATE_KWARGS)" -ForegroundColor Yellow
        }
        Write-Host "Note: a --chat-template-kwargs JSON parse error is a Windows PowerShell quoting bug, not a bad personality prompt. This script sets LLAMA_ARG_CHAT_TEMPLATE_KWARGS instead of a CLI flag to avoid that." -ForegroundColor Yellow
        Wait-IfStartupFailed
        exit $LASTEXITCODE
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
