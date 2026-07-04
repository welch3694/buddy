---
name: implement-issue
description: >-
  Implements a GitHub issue for welch3694/buddy — fetches issue details,
  creates a branch from main, plans, implements, runs tests, and updates the
  issue. Use when the user references a GitHub issue number, says implement/fix/
  work on/close issue #N, or asks to start development on a tracked issue.
---

# Implement GitHub Issue

End-to-end workflow for implementing a GitHub issue. **Source of truth is GitHub Issues only** — do not read or update a local `issues/` directory.

For issue creation, editing, or closing without implementation, use the `issue` skill (`.cursor/skills/issue/SKILL.md`).

## Repository

- **Owner/Repo**: `welch3694/buddy`
- **Default base branch**: `main`
- **CLI**: `gh` (authenticated, repo scope)

## Preconditions

- Agent mode (shell access required)
- Run from repo root
- Virtual environment at `.venv/`
- `gh` authenticated for `welch3694/buddy`

## Inputs

| Input | Required | Default |
|-------|----------|---------|
| `issue_number` | Yes | — |
| `base_branch` | No | `main` |

Extract `issue_number` from the user request (`#42`, `issue 42`, URL `.../issues/42`).

## Workflow

Track progress with this checklist:

```
Implement Issue Progress:
- [ ] Step 1: Fetch issue
- [ ] Step 2: Validate and mark in-progress
- [ ] Step 3: Create branch
- [ ] Step 4: Plan
- [ ] Step 5: Implement
- [ ] Step 6: Test
- [ ] Step 7: Update issue
- [ ] Step 8: Report summary
```

### Step 1: Fetch issue

```bash
gh issue view <NUMBER> --json number,title,body,state,labels,assignees,url,comments
```

Also fetch human-readable output when parsing acceptance criteria:

```bash
gh issue view <NUMBER>
gh issue view <NUMBER> --comments
```

Extract and restate in chat:

- **Title** and **URL**
- **Type** from labels (`bug`, `enhancement`, `task`, `question`) or title prefix
- **Priority** from `priority:*` labels
- **Component** labels (`speech`, `tools`, `memory`, `scripts`, `tests`, `deps`)
- **Acceptance criteria** — checkboxes under `## Acceptance Criteria` in the body; if missing, derive 1–3 concrete done conditions from Summary/Motivation
- **Implementation notes** — any `## Implementation Notes`, related files, or constraints in the body or comments

If the issue is **closed**, report that and ask whether to reopen or pick a different issue. Do not implement against a closed issue unless the user explicitly requests it.

If the issue is a **question** with no code work, answer or discuss instead of opening a branch.

### Step 2: Validate and mark in-progress

1. Confirm the issue is **open** and scoped to a single deliverable. If it is an epic, ask the user which slice to implement first or propose splitting via the `issue` skill.
2. Check for an existing branch or open PR linked in comments (`gh search prs "issue:<NUMBER>" --json number,title,state,url`).
3. Mark the issue in progress:

```bash
gh issue edit <NUMBER> --add-label "in-progress"
```

4. Post a start comment:

```bash
gh issue comment <NUMBER> --body "## Started
Working on this issue on branch \`issue/<NUMBER>-<short-slug>\`.
"
```

Use `.cursor/tmp/issue-comment.md` for longer comments (`--body-file`); path is gitignored.

### Step 3: Create branch

Branch name: `issue/<NUMBER>-<short-slug>` (lowercase, hyphens, max ~40 chars for slug).

```bash
git fetch origin <base_branch>
git checkout <base_branch>
git pull origin <base_branch>
git checkout -b issue/<NUMBER>-<short-slug>
```

If already on a feature branch with relevant work, confirm with the user before switching.

### Step 4: Plan

Before editing production code:

1. Search the codebase for related modules (labels and body hints).
2. Write a short plan to `.cursor/plans/issue-<NUMBER>-<short-slug>.md`:

```markdown
# Issue #<NUMBER>: <title>

## Goal
<one paragraph>

## Acceptance criteria
- [ ] ...

## Files to touch
- ...

## Test plan
- ...

## Risks / constraints
- llama-server must be running for speech integration tests
- buddy_tools patches speech-to-speech at runtime — verify tool registration after changes
```

3. Present the plan in chat and **wait for user approval** when:
   - Priority is `priority:high`
   - Multiple modules are affected
   - Requirements are ambiguous or acceptance criteria are missing

For small, well-specified bugs (`priority:low`, single file), proceed after stating the plan briefly.

Respect project rules in `.cursor/rules/` when present.

### Step 5: Implement

- Follow acceptance criteria literally; check off mentally as you go.
- Match existing patterns in the touched module; read neighboring code first.
- Add or update tests alongside logic changes when a `tests/` directory exists.
- **No legacy code**: delete replaced implementations; do not leave dead code "for compatibility".

Component hints:

| Label | Likely areas |
|-------|----------------|
| `speech` | `run_speech_to_speech.py`, speech-to-speech integration |
| `tools` | `buddy_tools/` |
| `memory` | `memory/`, `buddy_tools/memory.py` |
| `scripts` | `start-*.ps1`, `start-*.bat`, `setup-stable-venv.ps1` |
| `deps` | `requirements.txt`, `requirements-lock*.txt` |
| `tests` | `tests/` only unless fixing production bug |

For **speech / voice** changes, verify manually when appropriate:

1. Start llama-server (`start-llama-server-speech.bat`)
2. Run the agent (`start-speech-to-speech.ps1`)
3. Confirm the changed behavior in conversation or tool calls

### Step 6: Test

Run automated tests when they exist; otherwise use the manual test plan from Step 4.

| Changed area | Command |
|--------------|---------|
| Any Python logic (when `tests/` exists) | `.venv\Scripts\activate && python -m pytest tests/ -x` |
| Single test file | `.venv\Scripts\activate && python -m pytest tests/test_<module>.py -x` |
| No test suite yet | Manual verification per acceptance criteria and test plan |

Fix failures before updating the issue. Do not mark acceptance criteria complete while tests are red unless the user explicitly overrides.

If logic changed without test updates and a test suite exists, add tests or flag the gap in the issue comment.

For wrap-up (ticket branch, commit, PR to main), use the `session-finalize` skill when the user wants to close the session.

### Step 7: Update issue

After implementation passes tests:

1. Comment with summary and test command:

```bash
gh issue comment <NUMBER> --body "## Implementation complete
**Branch:** \`issue/<NUMBER>-<short-slug>\`
**Changes:** <1-2 sentences>
**Tests:** \`python -m pytest tests/ ...\` — pass (or manual verification — pass)

### Acceptance criteria
- [x] <criterion met>
- [ ] <criterion not yet met — explain why>
"
```

2. Edit the issue body to check off completed acceptance criteria when the body uses `- [ ]` checkboxes (use `--body-file` with the full updated body).

3. **Do not close the issue** until the user confirms or a PR is merged. Remove `in-progress` and add `needs-review` when ready for review:

```bash
gh issue edit <NUMBER> --remove-label "in-progress" --add-label "needs-review"
```

4. If the user asks to open a PR, use `gh pr create` and link the issue (`Closes #<NUMBER>` in the PR body).

### Step 8: Report summary

Post a brief summary in chat:

```markdown
## Issue #<NUMBER> — <title>

**URL:** <issue url>
**Branch:** `issue/<NUMBER>-<short-slug>`
**Status:** implemented / blocked / partial

**Done:**
- ...

**Tests:** pass / fail (command run)

**Issue updated:** comment posted; labels: ...

**Next:** open PR / user review / remaining criteria
```

## Decision guide

| Situation | Action |
|-----------|--------|
| Issue too large for one PR | Propose sub-issues via `issue` skill; implement one slice |
| Duplicate open issue exists | Stop; link duplicates in a comment |
| Blocked on design question | Comment on issue, add `blocked`, ask user |
| Needs review before merge | Self-review diff; offer deeper review if user asks |
| User says "just fix it quick" | Skip written plan file; still run targeted tests or manual checks |

## Error handling

| Error | Action |
|-------|--------|
| Issue not found | Verify number; `gh issue list --state all --limit 20` |
| Issue closed | Report state; ask to reopen or pick another |
| `gh: command not found` | Report; link `https://github.com/welch3694/buddy/issues/<NUMBER>` |
| Auth failure | Run `gh auth login` |
| Dirty working tree on checkout | Stash or commit; ask user if unclear |
| Tests fail | Fix or report blocker on the issue; do not claim completion |
| Branch already exists | Check it out if same issue; otherwise ask user |

## Related skills

| Skill | When |
|-------|------|
| `issue` | Create, edit, close, search issues |
| `session-finalize` | End-of-session commit and PR to main |

## Example

User: "Implement issue #12"

1. `gh issue view 12` → enhancement, `priority:medium`, `tools`
2. Add `in-progress`, comment started
3. `git checkout main && git pull && git checkout -b issue/12-add-calendar-tool`
4. Write `.cursor/plans/issue-12-add-calendar-tool.md`, implement in `buddy_tools/`
5. Run `python -m pytest tests/ -x` if tests exist; otherwise verify tool registration manually
6. Comment on issue, set `needs-review`, summarize in chat
