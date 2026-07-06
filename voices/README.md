# Voices

Named voice clones for Buddy (Qwen3 TTS). Each voice is a folder with:

- `audio.wav` — reference audio (about 3–15 seconds of clear single-speaker speech)
- `ref_text.txt` — transcript of what is spoken in the wav (must match closely for best clone quality)

A folder is a valid voice only when **both** files are present.

## Where voices live

At runtime, Buddy loads voices from your **user data directory**, not from this repo folder:

| Platform | Default `{BUDDY_DATA_DIR}/voices/` |
|----------|-------------------------------------|
| Windows | `%LOCALAPPDATA%\Buddy\voices\` |
| macOS | `~/Library/Application Support/Buddy/voices/` |
| Linux | `$XDG_DATA_HOME/buddy/voices/` or `~/.local/share/buddy/voices/` |

Override the root with `BUDDY_DATA_DIR` (see main `README.md`).

On first run, shipped defaults from this repo (e.g. `cliff/`) are copied into the data dir. Legacy voices that lived only under the repo `voices/` tree are migrated once when the user data dir is empty.

## Add a voice manually

1. Create `{BUDDY_DATA_DIR}/voices/{voice_id}/` (lowercase letters, numbers, `_`, `-`).
2. Add `audio.wav` and `ref_text.txt`.
3. Use the new id in a personality's `voice_id`, or ask Buddy to `switch_voice` to it.

New folders are discovered on the next `list_voices` call or voice switch — no restart required.

Example:

```
{BUDDY_DATA_DIR}/voices/
  cliff/
    audio.wav
    ref_text.txt
  narrator/
    audio.wav
    ref_text.txt
```

Default voice id: `cliff` (see `buddy_tools.voice.voices.DEFAULT_VOICE_ID`).

Default backend is Qwen3 (`$ttsBackend = "qwen3"` in `start-speech-to-speech.ps1`). Optional Pocket TTS uses preset catalog voices only; cloning from your own `audio.wav` requires accepting Kyutai's terms.

At runtime, `switch_voice` / personality switches call `buddy_tools.voice.session.apply_voice`, which updates the active TTS handler and the session voice path.
