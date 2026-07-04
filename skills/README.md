# Global built-in skills

Platform-wide guided workflows shipped with Buddy. Available to **every** personality at runtime — no need to copy skills into each persona folder.

## Layout

```
skills/                              # built-ins (repo, read-only at runtime)
  edit-personality/
    SKILL.md
    references/
{BUDDY_DATA_DIR}/skills/             # shared user skills (mutable, optionally scoped)
{BUDDY_DATA_DIR}/personalities/{id}/skills/   # per-persona skills (mutable)
```

Each skill follows the [Agent Skills layout](https://agentskills.io): a required `SKILL.md` with YAML frontmatter, plus optional `references/`, `scripts/`, and `assets/` subfolders.

## Runtime discovery

`list_skills` and `start_skill` resolve skills from three sources, merged by name:

1. **Built-ins** — this repo `skills/` directory (read at runtime via `get_repo_root()`)
2. **Shared user skills** — `{BUDDY_DATA_DIR}/skills/` (mutable; scoped to all personas or a subset)
3. **Persona skills** — `{BUDDY_DATA_DIR}/personalities/{id}/skills/`

### Name collisions

When names collide, precedence is:

**personality > shared > built-in**

- Built-ins are the platform default
- Shared user skills override built-ins for eligible personas
- Per-persona skills override both shared and built-in copies

### Shared skill scoping

Shared skills under `{BUDDY_DATA_DIR}/skills/` can apply to every personality or only selected ids. Set scope in `SKILL.md` frontmatter:

```yaml
metadata:
  buddy:
    personalities: all          # default when omitted — visible to every persona
    # personalities: [coach, buddy]   # visible only to listed personality ids
```

`list_skills` includes `source: "shared"` and a `scope` field (`"all"` or a list of ids) for shared skills.

### Mutability (design decision)

Built-in skills are **read-only platform capabilities**:

- Loaded from the repo on every lookup — **not** copied into the user data directory
- The agent cannot edit or delete built-in `SKILL.md` files through skill tools
- Updates ship with app releases; users get fixes without manual merges

User-authored skills belong under `{BUDDY_DATA_DIR}/skills/` (shared, with optional scoping) or `{BUDDY_DATA_DIR}/personalities/{id}/skills/` (persona-only). To customize a built-in workflow, create a shared or persona-scoped skill with the same name (override) or a new name (fork).

## Shipped built-ins

| Skill | Purpose |
|-------|---------|
| `edit-personality` | Safe guided edit of a persona's `prompt.md` |
| `remember` | Save a user fact with explicit global vs persona scope ("share with everyone" / "keep it between us") |

The **remember** skill auto-starts when the user says "remember that", "don't forget", or similar — the model calls `start_skill` with name `remember` rather than saving memory directly. It uses existing `append_memory` / `update_memory` tools with `scope: global` or `scope: persona`; no separate persistence layer.

## Adding a built-in skill

1. Create `skills/{skill-name}/SKILL.md` with valid frontmatter (`name` must match the directory)
2. Add tests in `tests/test_skills.py` if discovery or collision behavior is affected
3. Built-ins are available immediately after restart — no seeding step required

See `personalities/README.md` for checklist skill format and step syntax.
