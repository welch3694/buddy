@echo off
REM One-click launcher: llama-server + speech-to-speech + companion panel.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-buddy.ps1"
