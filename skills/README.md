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
| `live-director` | Pulse session: timed camera-switch cues (~3 min) with optional conversational fill between cues |

The **remember** skill auto-starts when the user says "remember that", "don't forget", or similar — the model calls `start_skill` with name `remember` rather than saving memory directly. It uses existing `append_memory` / `update_memory` tools with `scope: global` or `scope: persona`; no separate persistence layer.

## Pulse sessions vs checklist skills

| | Checklist (`type: checklist`) | Pulse (`type: pulse`) |
|---|------------------------------|------------------------|
| **Examples** | `remember`, `edit-personality` | `live-director` |
| **Who advances** | LLM calls `advance_skill` after user confirms each step | Python worker owns timing, rules, and cues |
| **State** | `skill_state.json` (step index) | `pulse_state.json` (vars, pending cues, fired rules) |
| **Config** | Steps in `SKILL.md` | Rules in `references/session.yaml` |
| **LLM role** | Guide interactive steps | Narrate directed cues; optional `[NO_OUTPUT]` on conversational pulses |

**`live-director`** is the reference pulse skill. See `skills/live-director/references/session.yaml` for a working config.

**Full `session.yaml` reference** (conditions, mutations, schedule, limits): [`buddy_tools/pulse/SESSION_YAML.md`](../buddy_tools/pulse/SESSION_YAML.md)

Start with `start_skill` and name `live-director` when the user says "go live" or "start director".

### Manual test plan (live-director)

1. Start llama-server and speech-to-speech (`start-llama-server-speech.ps1`, `start-speech-to-speech.ps1`).
2. Say **"start director"** or **"go live"** — confirm `start_skill` arms the pulse worker.
3. Wait ~3 minutes (or temporarily lower `180` → `30` in `session.yaml` for a faster check) — verify a camera-switch cue fires **after** you stop speaking, not mid-sentence.
4. Talk continuously through the interval — verify the cue defers until brief silence (or hits `mandatory_cue_max_defer_s`).
5. Between cues, stay quiet — verify optional conversational pulses may speak briefly or stay silent (`[NO_OUTPUT]`).
6. Say **"cancel skill"** — worker stops and `pulse_state.json` clears.

## Adding a built-in skill

1. Create `skills/{skill-name}/SKILL.md` with valid frontmatter (`name` must match the directory)
2. Add tests in `tests/test_skills.py` if discovery or collision behavior is affected
3. Built-ins are available immediately after restart — no seeding step required

See `personalities/README.md` for checklist skill format and step syntax. Pulse skills use `metadata.buddy.type: pulse` and `references/session.yaml` instead of `## Steps`.
