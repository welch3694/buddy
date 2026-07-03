@echo off
REM Launch speech-to-speech in a console that stays open on errors.
cd /d "%~dp0"
powershell.exe -NoProfile -NoExit -ExecutionPolicy Bypass -File "%~dp0start-speech-to-speech.ps1"
if errorlevel 1 (
    echo.
    echo Speech-to-speech failed. See errors above.
    pause
)
