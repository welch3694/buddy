# Install Buddy into the local .venv on Windows (avoids ggml / qwentts-cpp-python).
#
# speech-to-speech declares faster-qwen3-tts[ggml], which has no Windows wheels.
# We install locked transitive deps first, then speech-to-speech with --no-deps.
# Pocket TTS is the default voice backend; Qwen3 torch deps remain available if
# you set $ttsBackend = "qwen3" in start-speech-to-speech.ps1.
#
# Usage (from repo root, venv activated, PyTorch installed):
#   .\setup-venv.ps1
#
# Optional: target another venv
#   .\setup-venv.ps1 -VenvDir D:\Buddy\.venv

param(
    [string]$VenvDir = (Join-Path $PSScriptRoot ".venv")
)

$ErrorActionPreference = "Stop"
$devRoot = $PSScriptRoot
$venvPython = Join-Path (Resolve-Path $VenvDir).Path "Scripts\python.exe"
$lockDeps = Join-Path $devRoot "requirements-lock-deps.txt"
$lockFull = Join-Path $devRoot "requirements-lock.txt"
$s2s = "speech-to-speech @ git+https://github.com/huggingface/speech-to-speech.git@1e63f7e9343e491809d0d60e64f7ea551dbe845a"

if (-not (Test-Path $venvPython)) {
    Write-Error "Venv not found at $VenvDir — create and activate it first:`n  python -m venv .venv`n  .\.venv\Scripts\Activate.ps1`n  pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128"
}

if (-not (Test-Path $lockDeps)) {
    if (-not (Test-Path $lockFull)) {
        Write-Error "Missing requirements-lock.txt — cannot build dependency lock."
    }
    Write-Host "Generating requirements-lock-deps.txt from requirements-lock.txt..."
    Get-Content $lockFull |
        Where-Object { $_ -notmatch '^speech-to-speech' } |
        Set-Content $lockDeps -Encoding utf8
}

Write-Host "Installing locked dependencies (excluding speech-to-speech)..."
& $venvPython -m pip install -r $lockDeps

Write-Host "Installing Buddy requirements (opencv, mss)..."
& $venvPython -m pip install -r (Join-Path $devRoot "requirements.txt")

Write-Host "Installing speech-to-speech (no-deps, skips Linux-only ggml package)..."
& $venvPython -m pip install --no-deps $s2s

Write-Host "Verifying imports..."
Push-Location $devRoot
& $venvPython -c "import speech_to_speech; import pocket_tts; print('speech_to_speech OK'); print('pocket_tts OK')"
Pop-Location

Write-Host "Done. Start Buddy with .\start-speech-to-speech.ps1"
