# Install Buddy dependencies into a stable worktree venv on Windows.
# Usage (from dev repo): .\setup-stable-venv.ps1 -StableDir D:\Buddy
#
# Why not plain "pip install -r requirements.txt"?
# speech-to-speech pulls faster-qwen3-tts[ggml] -> qwentts-cpp-python, which has
# no Windows wheels. Use setup-venv.ps1 (dev) or this script (stable worktree):
# locked deps + requirements.txt + speech-to-speech --no-deps.

param(
    [Parameter(Mandatory = $true)]
    [string]$StableDir
)

$ErrorActionPreference = "Stop"
$devRoot = $PSScriptRoot
$stableRoot = (Resolve-Path $StableDir).Path
$venvPython = Join-Path $stableRoot ".venv\Scripts\python.exe"
$lockDeps = Join-Path $devRoot "requirements-lock-deps.txt"
$s2s = "speech-to-speech @ git+https://github.com/huggingface/speech-to-speech.git@1e63f7e9343e491809d0d60e64f7ea551dbe845a"

if (-not (Test-Path $venvPython)) {
    Write-Error "Stable venv not found at $stableRoot\.venv — create it first: python -m venv .venv"
}

if (-not (Test-Path $lockDeps)) {
    Write-Host "Generating requirements-lock-deps.txt from requirements-lock.txt..."
    Get-Content (Join-Path $devRoot "requirements-lock.txt") |
        Where-Object { $_ -notmatch '^speech-to-speech' } |
        Set-Content $lockDeps -Encoding utf8
}

Write-Host "Installing locked dependencies (excluding speech-to-speech)..."
& $venvPython -m pip install -r $lockDeps

Write-Host "Installing Buddy requirements (opencv, mss)..."
& $venvPython -m pip install -r (Join-Path $devRoot "requirements.txt")

Write-Host "Installing speech-to-speech (no-deps, skips Linux-only ggml package)..."
& $venvPython -m pip install --no-deps $s2s

Write-Host "Verifying import..."
Push-Location $stableRoot
& $venvPython -c "import speech_to_speech; print('speech_to_speech OK')"
Pop-Location

Write-Host "Done. Run Buddy from $stableRoot with .\start-buddy.bat"
