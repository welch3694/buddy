# stable-diffusion.cpp server for Buddy image gen/edit.
# Model: Phil2Sat/Qwen-Image-Edit-Rapid-AIO-GGUF (diffusion + VAE + Qwen2.5-VL).
# Paths come from .env (BUDDY_SD_*). Independent of llama-server (port 8080).

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

function Import-BuddyDotEnv {
    $envPath = Join-Path $PSScriptRoot ".env"
    if (-not (Test-Path $envPath)) {
        return
    }
    Get-Content -LiteralPath $envPath | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#")) {
            return
        }
        $eq = $line.IndexOf("=")
        if ($eq -lt 1) {
            return
        }
        $key = $line.Substring(0, $eq).Trim()
        $value = $line.Substring($eq + 1).Trim()
        if (
            ($value.StartsWith('"') -and $value.EndsWith('"')) -or
            ($value.StartsWith("'") -and $value.EndsWith("'"))
        ) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        $existing = [System.Environment]::GetEnvironmentVariable($key, "Process")
        if ([string]::IsNullOrEmpty($existing)) {
            [System.Environment]::SetEnvironmentVariable($key, $value, "Process")
        }
    }
}

function Get-EnvOrDefault([string]$Name, [string]$Default) {
    $raw = [System.Environment]::GetEnvironmentVariable($Name, "Process")
    if ([string]::IsNullOrWhiteSpace($raw)) {
        return $Default
    }
    return $raw.Trim()
}

function Resolve-ModelPath([string]$Value, [string]$ModelDir) {
    if ([System.IO.Path]::IsPathRooted($Value)) {
        return $Value
    }
    return (Join-Path $ModelDir $Value)
}

try {
    Import-BuddyDotEnv

    $serverExe = Get-EnvOrDefault "BUDDY_SD_SERVER_EXE" "D:\StableDiffusion\sd-server.exe"
    $modelDir = Get-EnvOrDefault "BUDDY_SD_MODEL_DIR" "D:\StableDiffusion\models"
    $hostAddr = Get-EnvOrDefault "BUDDY_SD_HOST" "127.0.0.1"
    $port = Get-EnvOrDefault "BUDDY_SD_PORT" "1234"

    # Phil2Sat Rapid AIO GGUF stack (not a single file — needs all three + mmproj).
    # VAE must be Comfy-Org safetensors; pig_*.gguf uses 5D tensors sd.cpp cannot load.
    $diffusionRel = Get-EnvOrDefault "BUDDY_SD_DIFFUSION" "diffusion\v71\qwen-rapid-nsfw-v7.1-Q4_K_M.gguf"
    $vaeRel = Get-EnvOrDefault "BUDDY_SD_VAE" "vae\qwen_image_vae.safetensors"
    $llmRel = Get-EnvOrDefault "BUDDY_SD_LLM" "text\Qwen2.5-VL-7B-Instruct-abliterated.Q4_K_M.gguf"
    $llmVisionRel = Get-EnvOrDefault "BUDDY_SD_LLM_VISION" "text\Qwen2.5-VL-7B-Instruct-abliterated.mmproj-Q8_0.gguf"

    $diffusion = Resolve-ModelPath $diffusionRel $modelDir
    $vae = Resolve-ModelPath $vaeRel $modelDir
    $llm = Resolve-ModelPath $llmRel $modelDir
    $llmVision = Resolve-ModelPath $llmVisionRel $modelDir

    if (-not (Test-Path -LiteralPath $serverExe)) {
        throw "sd-server not found at $serverExe. Set BUDDY_SD_SERVER_EXE in .env (see .env.example)."
    }
    foreach ($pair in @(
            @{ Name = "diffusion"; Path = $diffusion; Hint = "BUDDY_SD_DIFFUSION" },
            @{ Name = "vae"; Path = $vae; Hint = "BUDDY_SD_VAE" },
            @{ Name = "llm"; Path = $llm; Hint = "BUDDY_SD_LLM" },
            @{ Name = "llm_vision"; Path = $llmVision; Hint = "BUDDY_SD_LLM_VISION" }
        )) {
        if (-not (Test-Path -LiteralPath $pair.Path)) {
            throw "$($pair.Name) not found at $($pair.Path). Set $($pair.Hint) / BUDDY_SD_MODEL_DIR in .env."
        }
    }

    Write-Host "SD server: $serverExe"
    Write-Host "Diffusion: $diffusion"
    Write-Host "VAE: $vae"
    Write-Host "LLM: $llm"
    Write-Host "LLM vision: $llmVision"
    Write-Host "Listen: http://${hostAddr}:${port}/"
    Write-Host "API: http://${hostAddr}:${port}/v1/images/generations (and /v1/images/edits)"

    # Rapid/Lightning-tuned defaults: low CFG. --offload-to-cpu helps 16GB VRAM with Q4 stack.
    & $serverExe `
        --diffusion-model $diffusion `
        --vae $vae `
        --llm $llm `
        --llm_vision $llmVision `
        --cfg-scale 1.0 `
        --diffusion-fa `
        --offload-to-cpu `
        -v `
        --listen-ip $hostAddr `
        --listen-port $port

    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "sd-server exited with code $LASTEXITCODE." -ForegroundColor Red
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
