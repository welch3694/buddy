# Start llama-server and speech-to-speech in separate windows.
# Double-click start-buddy.bat or run: .\start-buddy.ps1

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

function Test-LlamaServer {
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:8080/v1/models" -UseBasicParsing -TimeoutSec 2
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

if (-not (Test-LlamaServer)) {
    Write-Host "Starting llama-server..."
    $llamaPs1 = Join-Path $root "start-llama-server-speech.ps1"
    Start-Process -FilePath "powershell.exe" -ArgumentList @(
        "-NoProfile", "-NoExit", "-ExecutionPolicy", "Bypass", "-File", "`"$llamaPs1`""
    ) -WorkingDirectory $root

    Write-Host "Waiting for llama-server on http://127.0.0.1:8080 ..."
    $deadline = (Get-Date).AddMinutes(5)
    $ready = $false
    while ((Get-Date) -lt $deadline) {
        if (Test-LlamaServer) {
            $ready = $true
            break
        }
        Start-Sleep -Seconds 2
    }

    if (-not $ready) {
        Write-Warning "llama-server did not respond within 5 minutes. Starting voice agent anyway."
    } else {
        Write-Host "llama-server is ready."
    }
} else {
    Write-Host "llama-server already running on port 8080."
}

Write-Host "Starting speech-to-speech..."
$voicePs1 = Join-Path $root "start-speech-to-speech.ps1"
Start-Process -FilePath "powershell.exe" -ArgumentList @(
    "-NoProfile", "-NoExit", "-ExecutionPolicy", "Bypass", "-File", "`"$voicePs1`""
) -WorkingDirectory $root

Write-Host "Starting companion panel..."
$companionPs1 = Join-Path $root "start-companion.ps1"
& $companionPs1 -LaunchOnly

Write-Host "Done. Close the llama-server, voice, and companion windows to stop Buddy."
