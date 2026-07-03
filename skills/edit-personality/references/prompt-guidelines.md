# prompt.md guidelines

`prompt.md` holds **persona only** — who the assistant is and how it should sound. Buddy assembles the full system prompt at runtime from several layers. Only the persona layer is stored on disk.

## Good prompt.md content

- Identity and display name cues ("You are Buddy, a warm and concise voice assistant.")
- Tone and style ("Keep answers brief unless asked to elaborate.")
- Role or domain ("Help with home-office setup and daily planning.")
- Personality traits the user requested ("Patient, encouraging, uses plain language.")

## Never put in prompt.md

- Fixed voice rules (TTS pacing, pronunciation boilerplate)
- Tool instructions (`list_memory`, `update_personality`, skill tool docs, etc.)
- Memory snapshot or "what you remember" sections from the live prompt
- Skill framework instructions or active skill context
- Listening pause, camera, or screen capture instructions
- The full stacked system prompt from `build_tool_instructions()`

## Editing tips

1. **Read before write** — call `read_personality` and inspect the current `prompt` field.
2. **Minimal diffs** — change only what the user asked for; do not rewrite unrelated sections.
3. **Short files** — a few paragraphs is normal; multi-page prompts usually mean runtime content leaked in.
4. **Confirm verbally** — restate changes before calling `update_personality`.

## Example (good)

```markdown
You are Buddy, a helpful voice assistant for John.

Be warm, concise, and practical. When explaining steps, go one at a time and wait for confirmation.

You enjoy light humor but stay focused on what the user needs.
```

## Example (bad — do not save)

```markdown
You are Buddy.

Tools: list_memory, read_memory, update_memory, ...
Fixed voice rules: ...
Memory snapshot: User prefers ...
Active skill context: ...
```

If you see content like the bad example on disk, replace it with a short persona-only version after confirming with the user.
