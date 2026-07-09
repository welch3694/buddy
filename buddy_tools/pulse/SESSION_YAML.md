# Pulse `session.yaml` reference

Pulse skills (`metadata.buddy.type: pulse`) store timing and cue logic in:

```text
skills/{skill-name}/references/session.yaml
```

At `start_skill`, the file is validated and snapshotted into `pulse_state.json` under `session_config`. **Edits to `session.yaml` do not affect a running session** — cancel and re-start the skill, or use `update_pulse_config` / `write_skill_file` and then re-start to pick up changes.

Reference implementation: `skills/live-director/references/session.yaml`.

---

## Voice tuning via `update_pulse_config`

Use the `update_pulse_config` tool to merge structured params without LLM-generated YAML. Hand-edited `rules`, `schedule`, and other `init.set` vars are preserved; only the keys below are updated.

| Param key | YAML path | Purpose |
|-----------|-----------|---------|
| `camera_switch_interval_s` | `init.set.switch_interval_s` | Seconds between camera-switch rule firings |
| `cameras` | `cameras` | List of `{ id, label }` for `$rotate(cameras)` |
| `conversation_min_silence_s` | `pulse.conversation_check_s` | Quiet time before conversational fill |
| `min_speak_interval_s` | `pulse.min_speak_interval_s` | Minimum gap between narrator speaks |
| `tick_interval_s` | `pulse.tick_interval_s` | Worker tick period |
| `mandatory_cue_max_defer_s` | `pulse.mandatory_cue_max_defer_s` | Max defer for mandatory cues |

**Note:** Re-serializing `session.yaml` after a param merge may drop YAML comments. Rule bodies and custom vars remain intact.

Example:

```json
{
  "camera_switch_interval_s": 300,
  "cameras": [{"id": 1, "label": "wide"}, {"id": 2, "label": "close"}],
  "conversation_min_silence_s": 30,
  "min_speak_interval_s": 45
}
```

---

## How it works

Each worker tick runs two steps:

1. **Rules / schedule** — evaluate conditions, update `vars`, queue `pending_cue`
2. **Injection** — if a cue is queued (or conversational gates allow), prompt the LLM to speak

Rules **do not speak directly**. A non-empty `cue:` queues text; the injection layer delivers it after user silence (and other gates). See `buddy_tools/pulse/gates.py` and `inject.py`.

Conversational fill between mandatory cues is **not** a YAML rule — it is driven by `pulse.conversation_check_s` and related gate settings.

---

## Top-level keys

| Key | Required | Purpose |
|-----|----------|---------|
| `name` | yes | Session name (usually matches skill name) |
| `pulse` | no | Worker tick interval and injection gate overrides |
| `init.set` | no | Initial runtime vars and `phase` |
| `cameras` | no | List of `{ id, label }` for `$rotate(cameras)` and cue `{label}` |
| `rules` | no | Declarative timed / conditional rules |
| `schedule` | no | One-shot absolute cues at session seconds |

---

## `pulse:` timing

| Key | Default | Purpose |
|-----|---------|---------|
| `tick_interval_s` | `5` | Worker tick period (seconds) |
| `conversation_check_s` | `60` | Min seconds between conversational pulse attempts |
| `min_speak_interval_s` | `45` | Min seconds since last assistant speech before optional chat |
| `mandatory_cue_max_defer_s` | `30` | Force-fire mandatory cue after this defer (even if user is talking) |

---

## `init.set` — runtime vars

Arbitrary key/value pairs copied into `pulse_state.json` → `vars` at session start.

Special keys:

| Key | Behavior |
|-----|----------|
| `phase` | Also stored on `PulseState.phase` (usable in `when: phase == …`) |
| `narrator_muted` | Also stored on `PulseState.narrator_muted` |

**Auto-seeded** if omitted (session start timestamp):

- `last_camera_switch_at`
- `last_conversation_pulse_at`

Use ISO timestamp vars (set `"$now"` or rely on seeding) as anchors for `elapsed_since(...)`.

---

## `rules:` entries

```yaml
rules:
  - id: my-rule              # required, unique string
    when: <condition>          # required — see below
    once: false                # optional; default false
    priority: mandatory        # optional; mandatory | conversational
    set:                       # optional; var mutations
      some_var: 42
      anchor: "$now"
    cue: "Say this."           # optional; queues pending_cue when non-empty
```

| Field | Notes |
|-------|-------|
| `when` | Checked every tick. All clauses must pass when using `&&`. |
| `once` | If `true`, rule fires at most once per session (`fired_rules` tracks id). |
| `set` | Applied before `cue` on the same fire. Supports `$…` mutations. |
| `cue` | Interpolates `{var}` placeholders. Empty string does not queue speech. |
| `priority` | `mandatory` cues take precedence over conversational injection. |

Rules are evaluated **in file order** each tick.

---

## `when:` conditions

### Simple forms

| Form | Example | Meaning |
|------|---------|---------|
| Elapsed since timestamp var | `elapsed_since(last_camera_switch_at) >= 180` | Seconds since var (or `started_at`) |
| Var threshold | `elapsed_since(last_camera_switch_at) >= switch_interval_s` | Right-hand side may be a var name |
| Session age | `session_elapsed >= 1800` | Seconds since session `started_at` (sugar for `elapsed_since(started_at)`) |
| Phase equality | `phase == live` | String compare |
| Var equality | `pace == fast` | String/number/bool compare |
| Numeric compare | `switch_interval_s >= 120` | Var compared to number or var name |

Timestamp vars must contain ISO-8601 UTC strings (as produced by `"$now"` or auto-seed).

### Compound AND

Join conditions with `&&` (all must pass):

```yaml
when: phase == late && elapsed_since(last_camera_switch_at) >= switch_interval_s
```

### Not supported in `when:`

- `||` (OR), `!` (NOT), parentheses
- Arithmetic (`switch_interval_s - 5`)
- `$…` mutations (use `set:` instead)

---

## `set:` mutations

String values starting with `$` are evaluated expressions. Arguments may be **numeric literals**, **var names**, or **nested `$…` calls**.

| Mutation | Args | Returns | Example |
|----------|------|---------|---------|
| `$now` | — | ISO UTC timestamp | `last_switch_at: "$now"` |
| `$rotate(name)` | list name | Next item in list | `current_camera: "$rotate(cameras)"` |
| `$add(a, b)` | 2 | Sum | `$add(switch_interval_s, 10)` |
| `$sub(a, b)` | 2 | Difference | `$sub(switch_interval_s, 5)` |
| `$min(a, b)` | 2 | Smaller value | `$min(switch_interval_s, 60)` |
| `$max(a, b)` | 2 | Larger value | `$max(switch_interval_s, 120)` |
| `$clamp(v, min)` | 2 | Floor — `max(v, min)` | `$clamp(value, 60)` |
| `$clamp(v, min, max)` | 3 | Clamp to range | `$clamp(value, 60, 180)` |

Whole-number results are stored as integers; otherwise float.

Invalid mutations log a warning and leave the target var unchanged.

### Nested example — progressive tighten

```yaml
init:
  set:
    switch_interval_s: 180
    min_switch_interval_s: 60
    tighten_step_s: 5

rules:
  - id: camera-switch
    when: elapsed_since(last_camera_switch_at) >= switch_interval_s
    set:
      current_camera: "$rotate(cameras)"
      last_camera_switch_at: "$now"
      switch_interval_s: "$clamp($sub(switch_interval_s, tighten_step_s), min_switch_interval_s)"
    cue: "Switch to camera {current_camera} — {label}."
    priority: mandatory
```

Each fire reduces the interval by `tighten_step_s` until `min_switch_interval_s`.

---

## `cue:` interpolation

Cue strings support `{var_name}` substitution from runtime `vars` plus:

| Placeholder | Source |
|-------------|--------|
| `{phase}` | Current phase |
| `{label}` | Label of `{current_camera}` from `cameras` list |

No formatting or expressions inside cues.

---

## `schedule:` — absolute timeline

One-shot entries fired when session elapsed time reaches `at_s`:

```yaml
schedule:
  - at_s: 30
    id: t30                    # optional; default schedule-{at_s}
    cue: "Thirty second mark."
    priority: mandatory
```

Each entry fires once. Schedule is evaluated before `rules` each tick.

---

## `cameras:` list

```yaml
cameras:
  - { id: 1, label: "wide shot" }
  - { id: 2, label: "close-up" }
```

Used by `$rotate(cameras)` and `{label}` in cues. Customize ids/labels freely; keep `id` + `label` for interpolation.

---

## Runtime state

Live state lives at:

```text
{BUDDY_DATA_DIR}/memory/{persona_namespace}/pulse_state.json
```

Useful fields: `vars`, `pending_cue`, `phase`, `started_at`, `tick_count`, `session_config` (snapshot).

---

## Examples

### One-time pace change after 30 minutes

```yaml
rules:
  - id: tighten-pace
    when: session_elapsed >= 1800
    once: true
    set:
      switch_interval_s: 120
      last_camera_switch_at: "$now"
  - id: camera-switch
    when: elapsed_since(last_camera_switch_at) >= switch_interval_s
    # ...
```

Place transition rules **before** repeating rules so timer resets apply on the same tick.

### Scripted milestone

```yaml
schedule:
  - at_s: 600
    cue: "Ten minutes in — great pace."
    priority: mandatory
```

---

## Limitations (explicit)

| Not supported | Workaround |
|---------------|------------|
| OR / NOT in `when` | Separate rules; use `phase` / vars |
| Arithmetic in `when` | Store computed value in `set:` |
| `$mul`, `$div` | Use `$add` / `$sub` chains (or extend engine) |
| Rule triggered by another rule firing | Share state via vars set in `set:` |
| Live reload of `session.yaml` | Cancel + re-start skill (or `update_pulse_config` then re-start) |
| Empty `cue` rule to trigger conversational speech | Use gate-driven conversational pulses |

---

## See also

- `skills/README.md` — pulse vs checklist overview
- `skills/live-director/SKILL.md` — narrator behavior
- `buddy_tools/pulse/rules.py` — condition and mutation implementation
- `buddy_tools/pulse/gates.py` — silence / defer injection gates
