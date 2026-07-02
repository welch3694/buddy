@echo off
REM One-click launcher: llama-server + speech-to-speech (two windows).
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-buddy.ps1"
