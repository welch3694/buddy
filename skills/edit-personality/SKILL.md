---
name: edit-personality
description: Safely update a personality's prompt.md with persona-only content. Use when the user asks to change traits, tone, role, or identity — not when adjusting tools or system behavior.
metadata:
  buddy:
    type: checklist
---

# Edit personality

Guide the user through a safe personality edit. Only **persona content** belongs in `prompt.md` — identity, tone, role, and traits. Runtime layers (voice rules, tool docs, memory snapshot, skill instructions, listening pause, camera/screen) are injected separately and must never be written to disk.

Use `read_personality` to load on-disk content before editing. Use `update_personality` with a minimal `prompt` patch — never paste the live system prompt stack.

See `references/prompt-guidelines.md` for good vs bad examples.

## Steps

### confirm-target
Which personality should we edit? Default to the active one unless the user named another. Call `read_personality` (or `list_personalities` if needed) and briefly summarize the current persona in your own words — do not read the full prompt aloud unless asked.

### confirm-changes
What should change? Restate the desired traits or tone in one or two sentences and confirm with the user before writing anything.

### draft-prompt
Draft the new `prompt.md` content: short, persona-only prose. Exclude tool names, memory instructions, fixed voice rules, and anything from the assembled session instructions. Prefer editing the existing prompt over replacing it entirely.

### apply-update
Call `update_personality` with only the fields that changed (usually `prompt`). After success, confirm what was saved in plain language — do not claim success without a tool result.
