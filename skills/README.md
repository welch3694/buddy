# Global built-in skills

Platform-wide guided workflows shipped with Buddy. Available to **every** personality at runtime — no need to copy skills into each persona folder.

## Layout

```
skills/
  edit-personality/
    SKILL.md
    references/
personalities/{id}/skills/     # optional per-persona skills (user data dir at runtime)
```

Each skill follows the [Agent Skills layout](https://agentskills.io): a required `SKILL.md` with YAML frontmatter, plus optional `references/`, `scripts/`, and `assets/` subfolders.

## Runtime discovery

`list_skills` and `start_skill` resolve skills from two sources, merged by name:

1. **Built-ins** — this repo `skills/` directory (read at runtime via `get_repo_root()`)
2. **Persona skills** — `{BUDDY_DATA_DIR}/personalities/{id}/skills/`

### Name collisions

If a persona defines a skill with the same name as a built-in, the **persona skill wins**. Built-ins are the default; per-persona copies are for customization or agent-authored workflows.

### Mutability (design decision)

Built-in skills are **read-only platform capabilities**:

- Loaded from the repo on every lookup — **not** copied into the user data directory
- The agent cannot edit or delete built-in `SKILL.md` files through skill tools
- Updates ship with app releases; users get fixes without manual merges

Agent- or user-authored skills belong under `{BUDDY_DATA_DIR}/personalities/{id}/skills/` only. To customize a built-in workflow, create a persona-scoped skill with the same name (override) or a new name (fork).

## Adding a built-in skill

1. Create `skills/{skill-name}/SKILL.md` with valid frontmatter (`name` must match the directory)
2. Add tests in `tests/test_skills.py` if discovery or collision behavior is affected
3. Built-ins are available immediately after restart — no seeding step required

See `personalities/README.md` for checklist skill format and step syntax.
