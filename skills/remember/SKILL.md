---
name: remember
description: >-
  Save a fact the user wants remembered. Call start_skill immediately when they
  say "remember that", "don't forget", "keep in mind", or similar — do not save
  memory until this workflow completes. Guides confirm → scope choice → save →
  spoken confirmation.
metadata:
  buddy:
    type: checklist
---

# Remember

Voice-friendly workflow for saving a fact to persistent memory. Walk one step at a time; wait for verbal confirmation before calling `advance_skill`.

**Trigger:** When the user asks you to remember something, call `start_skill` with name `remember` before using memory tools. Do not call `append_memory` or `update_memory` until the scope step is complete.

## Steps

### confirm-fact
Restate what they want remembered in plain, conversational language — one or two short sentences. Ask whether you got it right. Do not save anything yet.

### choose-scope
Ask naturally: should this be **shared with everyone** (all personas) or **kept between us** (this personality only)?

- **Share with everyone** → save with `scope: global` (`memory/global/notes.md`)
- **Keep it between us** → save with `scope: persona` (active personality's `memory/{persona}/notes.md`)

Wait for a clear answer before advancing.

### save-memory
Save to document `notes` using the scope from the previous step:

1. Optionally call `read_memory` with the chosen scope to check for an existing line about the same topic.
2. Prefer `update_memory` when replacing or correcting an existing topic (pass `name`, `topic`, `value`, and `scope`).
3. Use `append_memory` only for a genuinely new standalone fact that does not replace anything already stored (pass `name`, `content`, and `scope`).

Do not call both tools unless the user gave multiple distinct facts.

### confirm-saved
Confirm briefly in spoken language where it was saved — e.g. "Got it, I'll remember that for everyone" or "Got it, that's just between us." Do not mention tool names, files, or paths. Then call `advance_skill` to finish the skill.
