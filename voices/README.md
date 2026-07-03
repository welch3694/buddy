# Voices

Named voice clones for Buddy (Qwen3 TTS). Each voice is a folder under `voices/` with:

- `audio.wav` — reference audio (about 3–15 seconds of clear single-speaker speech)
- `ref_text.txt` — transcript of what is spoken in the wav (must match closely for best clone quality)

A folder is a valid voice only when **both** files are present.

Default backend is Qwen3 (`$ttsBackend = "qwen3"` in `start-speech-to-speech.ps1`). Optional Pocket TTS uses preset catalog voices only; cloning from your own `audio.wav` requires accepting Kyutai's terms.

Example:

```
voices/
  cliff/
    audio.wav
    ref_text.txt
```

Default voice id: `cliff` (see `buddy_tools.voices.DEFAULT_VOICE_ID`).

At runtime, `switch_voice` / personality switches call `buddy_tools.voice_session.apply_voice`, which updates the active TTS handler and the session voice path.
