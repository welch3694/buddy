# Personalities

Named behavior profiles for the Buddy voice assistant. Each personality is a folder under `personalities/` with:

- `profile.yaml` — metadata (name, voice, behaviors, memory namespace)
- `prompt.md` — persona and tone instructions

A folder is a valid personality only when **both** files are present.

## Active personality

`personalities/active.json` stores the active personality id (default: `buddy`). `start-speech-to-speech.ps1` loads the active profile at startup.

## profile.yaml

```yaml
id: buddy
name: Buddy
description: Helpful voice assistant
voice_id: cliff          # references voices/{voice_id}/
behaviors:
  verbosity: default
  warmth: high
memory_namespace: buddy  # per-persona memory (phase 2)
```

The `id` must match the folder name. `voice_id` must reference a valid voice in `voices/`.

## Adding a personality

1. Create `personalities/your_id/profile.yaml`
2. Create `personalities/your_id/prompt.md`
3. Optionally set `"id": "your_id"` in `active.json`

Or use `buddy_tools.personality.create_personality()` programmatically.
