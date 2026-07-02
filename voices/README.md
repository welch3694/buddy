# Voices

Named voice clone pairs for Qwen3 TTS Base. Each voice is a folder under `voices/` with:

- `audio.wav` — reference audio (about 3–15 seconds of clear single-speaker speech)
- `ref_text.txt` — transcript of what is spoken in the wav (must match closely for best clone quality)

A folder is a valid voice only when **both** files are present. Add a new voice by creating `voices/your_name/` with those two files — no registry or config required.

Example:

```
voices/
  cliff/
    audio.wav
    ref_text.txt
```

Default voice id: `cliff` (see `buddy_tools.voices.DEFAULT_VOICE_ID`).

At runtime, switching to a voice folder updates both `audio.wav` and `ref_text.txt` together (see `buddy_tools.voice_session.apply_voice`).
