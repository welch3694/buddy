# Sci-fi companion panel (Vite + React on http://127.0.0.1:5173).
# Run standalone or via start-buddy.ps1 / start-speech-to-speech.ps1.
#
# Usage:
#   .\start-companion.ps1              # run dev server in this window
#   .\start-companion.ps1 -LaunchOnly  # spawn a new window and return

param(
    [switch]$LaunchOnly
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$companionDir = Join-Path $root "companion"
$panelUrl = "http://127.0.0.1:5173"

function Test-CompanionPanel {
    try {
        $response = Invoke-WebRequest -Uri $panelUrl -UseBasicParsing -TimeoutSec 2
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Wait-IfStartupFailed {
    if ($Host.Name -ne "ConsoleHost") {
        return
    }
    Write-Host ""
    Write-Host "Press Enter to close this window..." -ForegroundColor Yellow
    [void][System.Console]::ReadLine()
}

function Start-CompanionDevServer {
    if (-not (Test-Path $companionDir)) {
        throw "Companion app not found at $companionDir"
    }

    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        throw "npm not found on PATH. Install Node.js (https://nodejs.org) to run the companion panel."
    }

    Push-Location $companionDir
    try {
        if (-not (Test-Path "node_modules")) {
            Write-Host "Installing companion dependencies (npm install)..."
            npm install
            if ($LASTEXITCODE -ne 0) {
                throw "npm install failed with exit code $LASTEXITCODE"
            }
        }

        if (Test-CompanionPanel) {
            Write-Host "Companion panel already running on $panelUrl"
            return
        }

        Write-Host "Starting companion panel on $panelUrl ..."
        npm run dev
        if ($LASTEXITCODE -ne 0) {
            throw "npm run dev failed with exit code $LASTEXITCODE"
        }
    } finally {
        Pop-Location
    }
}

try {
    if ($LaunchOnly) {
        if (Test-CompanionPanel) {
            Write-Host "Companion panel already running on $panelUrl"
            return
        }

        $self = Join-Path $root "start-companion.ps1"
        Start-Process -FilePath "powershell.exe" -ArgumentList @(
            "-NoProfile", "-NoExit", "-ExecutionPolicy", "Bypass", "-File", "`"$self`""
        ) -WorkingDirectory $root
        Write-Host "Companion panel starting in a new window ($panelUrl)."
        return
    }

    Start-CompanionDevServer
} catch {
    Write-Host ""
    Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
    if ($_.ScriptStackTrace) {
        Write-Host $_.ScriptStackTrace -ForegroundColor DarkRed
    }
    Wait-IfStartupFailed
    exit 1
}
