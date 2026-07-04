---
name: create-skill
description: >-
  Author a new guided workflow skill on disk. Use when the user asks to create,
  add, or write a skill, checklist, or workflow — not when running an existing one.
metadata:
  buddy:
    type: checklist
---

# Create skill

Guide the user through authoring a new skill safely. Skills live as `SKILL.md` files with YAML frontmatter. **Default:** write under the active personality's `skills/` folder. Use **shared** scope only when the user explicitly wants a cross-persona skill.

Do not guess paths or write files manually — always call `create_skill` so the file lands where discovery expects it.

## Steps

### confirm-purpose
What should this skill do? Restate the goal in one or two sentences and confirm with the user before drafting.

### draft-metadata
Pick a skill **name** (lowercase letters, digits, hyphens — must match the folder name), a **description** (when the model should use it), and whether it is a **checklist** (with `## Steps` and `### step-id` headings) or a **generic** workflow.

### confirm-scope
Ask where the skill should live unless the user already specified:

- **This personality** (default) → `create_skill` with default `scope: persona`
- **Shared across personas** → `create_skill` with `scope: shared` (only when they want it available to everyone or named personas)

Do not use shared scope unless the user opts in.

### draft-body
Draft the markdown **body** (content after frontmatter): title, instructions, and for checklists a `## Steps` section with one `### step-id` block per step. Keep steps voice-friendly — short prompts the assistant can read aloud.

### apply-create
Call `create_skill` with `name`, `description`, `body`, and the chosen `scope` / `skill_type`. After success, call `list_skills` and confirm the new skill appears with `source: personality` or `source: shared`.
