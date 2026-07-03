# Personalities

Named behavior profiles for the Buddy voice assistant.

## Repo templates vs runtime data

The repo `personalities/` folder holds **shipped templates** only (e.g. `buddy/profile.yaml` and `prompt.md`). At startup, any template missing from your user data directory is copied in automatically.

**Runtime personalities** live under your Buddy data directory:

| Platform | Default `BUDDY_DATA_DIR` |
|----------|--------------------------|
| Windows | `%LOCALAPPDATA%\Buddy\personalities\` |
| macOS | `~/Library/Application Support/Buddy/personalities/` |
| Linux | `$XDG_DATA_HOME/buddy/personalities/` or `~/.local/share/buddy/personalities/` |

Override with the `BUDDY_DATA_DIR` environment variable before starting Buddy. Point it at a Dropbox, Google Drive, or OneDrive folder for automatic backup via cloud sync.

Each personality folder contains:

- `profile.yaml` — metadata (name, voice, behaviors, memory namespace)
- `prompt.md` — persona and tone instructions
- `skills/` — optional guided workflows (Agent Skills layout; see below)

A folder is a valid personality only when **both** `profile.yaml` and `prompt.md` are present.

## Active personality

`{BUDDY_DATA_DIR}/personalities/active.json` stores the active personality id. If missing, Buddy defaults to `buddy`. The file is created when you switch personas during conversation or on first explicit switch.

## Seeding and reset

- **First run:** shipped templates (e.g. `buddy`) are copied into your data dir.
- **Edit Buddy:** changes are saved in the data dir like any other persona.
- **Reset factory Buddy:** delete `{BUDDY_DATA_DIR}/personalities/buddy/` and restart — the template is copied again from the repo.
- **New shipped personas:** after a repo update, restart Buddy; any template not already in your data dir is seeded automatically.

## profile.yaml

```yaml
id: buddy
name: Buddy
description: Helpful voice assistant
voice_id: cliff          # references voices/{voice_id}/
behaviors:
  verbosity: default
  warmth: high
memory_namespace: buddy
```

The `id` must match the folder name. `voice_id` must reference a valid voice in `voices/`.

## Skills (Agent Skills layout)

Each personality may define repeatable skills under `{data_dir}/personalities/{id}/skills/{skill-name}/`:

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

Active skill progress is stored in `{BUDDY_DATA_DIR}/memory/{namespace}/skill_state.json` (not global memory). Switching away from a persona mid-checklist preserves state in that persona's namespace; switching back allows resume.

Skill tools (`list_skills`, `start_skill`, `advance_skill`, etc.) are registered globally but resolve against the **active** personality's `skills/` folder in the data dir.

## Adding a personality

Ask Buddy to create one by voice (`create_personality`), or use `buddy_tools.personality.create_personality()` programmatically. New personas are written to your data dir.

To add a **shipped template** for all users, add `personalities/your_id/` to the repo with `profile.yaml` and `prompt.md`; it seeds on next startup for users who do not already have that id.
