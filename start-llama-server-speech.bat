@echo off
REM Gemma 4 llama-server profile tuned for speech-to-speech voice chat.
REM Model + mmproj live in D:\Llama\Models\ (change paths below if needed).
REM mmproj enables image input for multimodal / vision requests.
REM
REM Start this first, then run start-speech-to-speech.ps1 in the other terminal.

set "MODEL_DIR=D:\Llama\Models"
set "MODEL=%MODEL_DIR%\gemma-4-12b-it-uncensored-Q4_K_M.gguf"
set "MMPROJ=%MODEL_DIR%\mmproj-gemma-4-12B-it-bf16.gguf"

REM D:\Llama\llama-server.exe -m %MODEL_DIR%\gemma-4-E4B-it-Q4_K_M.gguf --mmproj %MMPROJ% ^
REM --cache-type-k q8_0 ^
REM --cache-type-v q8_0 ^

D:\Llama\llama-server.exe -m %MODEL% ^
    --mmproj %MMPROJ% ^
    --n-gpu-layers 99 ^
    --ctx-size 8192 ^
    --reasoning off ^
    --temperature 0.3 ^
    --repeat-penalty 1.1 ^
    --min-p 0.1 ^
    --host 0.0.0.0 --port 8080 ^
    --flash-attn on ^
    --chat-template-kwargs "{\"preserve_thinking\":false}" ^
    --log-colors on
