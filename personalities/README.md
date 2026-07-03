# Personalities

Named behavior profiles for the Buddy voice assistant. Each personality is a folder under `personalities/` with:

- `profile.yaml` — metadata (name, voice, behaviors, memory namespace)
- `prompt.md` — persona and tone instructions
- `skills/` — optional guided workflows (Agent Skills layout; see below)

A folder is a valid personality only when **both** `profile.yaml` and `prompt.md` are present.

## Active personality

`personalities/active.json` stores the active personality id (default: `buddy`). `start-speech-to-speech.ps1` loads the active profile at startup. During conversation, ask Buddy to switch personas (e.g. "switch to coach") or create new ones by voice.

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

## Skills (Agent Skills layout)

Each personality may define repeatable skills under `personalities/{id}/skills/{skill-name}/`:

```
personalities/coach/skills/
  equipment-setup/
    SKILL.md              # required — frontmatter + instructions
    references/           # optional — detailed checklists, forms
    scripts/              # optional — helper scripts
    assets/               # optional — templates, diagrams
```

### SKILL.md frontmatter

| Field | Required | Notes |
|-------|----------|-------|
| `name` | Yes | Lowercase + hyphens; must match the parent directory name |
| `description` | Yes | What the skill does and when to use it |
| `metadata.buddy.type` | No | Set to `checklist` for step-tracked workflows |

### Checklist skills

For guided step-by-step workflows, set `metadata.buddy.type: checklist` and define ordered steps with `### {step-id}` headings under a `## Steps` section:

```markdown
---
name: equipment-setup
description: Guide the user through pre-session rig checks. Use when they say set up or prep the rig.
metadata:
  buddy:
    type: checklist
---

# Equipment setup

Walk the user through one step at a time. Wait for verbal confirmation before advancing.

## Steps

### mic
Is your microphone connected and selected as the input device?

### headphones
Put on headphones to avoid feedback.
```

### Runtime state

Active skill progress is stored in `memory/{namespace}/skill_state.json` (not global memory). Switching away from a persona mid-checklist preserves state in that persona's namespace; switching back allows resume.

Skill tools (`list_skills`, `start_skill`, `advance_skill`, etc.) are registered globally but resolve against the **active** personality's `skills/` folder.

## Adding a personality

1. Create `personalities/your_id/profile.yaml`
2. Create `personalities/your_id/prompt.md`
3. Optionally add skills under `personalities/your_id/skills/`
4. Optionally set `"id": "your_id"` in `active.json`

Or use `buddy_tools.personality.create_personality()` programmatically.
