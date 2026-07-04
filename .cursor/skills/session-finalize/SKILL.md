---
name: session-finalize
description: >-
  End-of-session wrap-up for Buddy — ensure ticket branch, review the diff,
  run tests, commit, and open a PR to main. Use when the user agrees
  to wrap up, says finalize, or when session-finalize rule triggers after task completion.
---

# Session Wrap-Up

Close out a coding session cleanly: land changes on a ticket branch, run tests, commit, and open a PR.

## Preconditions

- Agent mode (shell access required)
- Run from repo root
- User has agreed to wrap-up (this counts as approval to commit and open a PR)
- `gh` authenticated for `welch3694/buddy`

## Workflow

Track progress with this checklist:

```
Wrap-up Progress:
- [ ] Step 1: Survey changes
- [ ] Step 2: Ensure ticket branch
- [ ] Step 3: Review diff
- [ ] Step 4: Run tests
- [ ] Step 5: Commit and open PR
- [ ] Step 6: Report summary
```

### Step 1: Survey changes

Run in parallel:

```bash
git status
git diff --stat
git branch --show-current
```

Note:

- Which areas changed (`buddy_tools/`, `run_speech_to_speech.py`, `memory/`, scripts, tests, etc.)
- Whether test files were added or updated alongside logic changes
- Current branch name
- Linked issue number (from branch name, conversation, or `.cursor/plans/issue-<N>-*.md`)

### Step 2: Ensure ticket branch

Ticket branches follow the implement-issue convention: `issue/<NUMBER>-<short-slug>` (lowercase, hyphens).

**Already on a ticket branch** (`issue/<NUMBER>-*`): proceed.

**Not on a ticket branch** (e.g. `main` or a generic branch):

1. Determine the issue number from conversation context, plan files, or ask the user.
2. Derive a short slug from the issue title or change summary (max ~40 chars).
3. Create and switch to the branch from `main`:

```bash
git fetch origin main
git checkout main
git pull origin main
git checkout -b issue/<NUMBER>-<short-slug>
```

If there are uncommitted changes on the current branch, either:

- Stash, create the ticket branch from `main`, and pop the stash, or
- Rename/move work to the new branch without losing changes (prefer `git stash` when switching bases is messy).

If the issue number is unclear, ask once before creating the branch.

### Step 3: Review diff

Choose depth based on change size:

| Change size | Review approach |
|-------------|-----------------|
| Small (few files, localized) | Quick self-review: read `git diff`, scan for obvious bugs, missing edge cases, style violations |
| Medium or cross-cutting | Thorough self-review; flag anything needing user attention before merge |

**Quick review checklist:**

- [ ] Logic handles edge cases and errors sensibly
- [ ] Tool registration and speech-to-speech patches still coherent after changes
- [ ] Tests cover new or changed behavior (flag gap if logic changed without test updates)
- [ ] No debug leftovers, commented-out code, or unrelated changes

Fix critical issues found during review before committing. Report non-blocking suggestions in the final summary.

### Step 4: Run tests

Run automated tests when a test suite exists; otherwise note manual verification from the issue or plan.

```bash
.venv\Scripts\activate
python -m pytest tests/
```

If `tests/` does not exist yet, skip automated tests and document manual verification steps in the PR test plan instead.

- **Pass:** proceed to Step 5.
- **Fail:** fix failures before committing when the cause is clear and in scope. Re-run until green. If failures look unrelated or ambiguous, report them and ask the user whether to fix now or defer.

Note the result (pass/fail/skip, count, duration) for the wrap-up summary and PR test plan.

### Step 5: Commit and open PR

#### 5a. Commit

Follow the user's git commit rule:

1. Run `git status`, `git diff`, and `git log -1` in parallel
2. Stage relevant files (never secrets, logs, or `__pycache__`)
3. Commit with a concise message focused on *why* (via HEREDOC)
4. Run `git status` to verify

If there are no changes to commit, skip to Step 6 and report a clean working tree.

#### 5b. Push and open PR

Base branch: **`main`**.

Run in parallel to prepare the PR:

```bash
git status
git diff --stat
git branch -vv
git log main...HEAD --oneline
```

Then push and create the PR:

```bash
git push -u origin HEAD
gh pr create --base main --title "<concise title>" --body "$(cat <<'EOF'
## Summary
- <1-3 bullets describing the change>

## Test plan
- [x] Automated tests passed during wrap-up (`python -m pytest tests/`)
- [ ] <manual verification steps from issue acceptance criteria or plan file, if any>

Closes #<NUMBER>
EOF
)"
```

PR title: match issue title or a short description of the fix/feature.

Link the issue with `Closes #<NUMBER>` when an issue number is known.

After creating the PR, optionally update the issue:

```bash
gh issue edit <NUMBER> --remove-label "in-progress" --add-label "needs-review"
```

Do not close the issue — merging the PR will do that via `Closes #`.

### Step 6: Report summary

Post a brief wrap-up report:

```markdown
## Wrap-up complete

**Branch:** `issue/<NUMBER>-<short-slug>`
**Changes:** [one-line summary]
**Review:** [pass / issues found and fixed / issues deferred]
**Tests:** [passed / failed / manual verification — details]
**Commit:** [committed `<hash>` / skipped — nothing to commit]
**PR:** [#<number>](<url>) → `main`
```

If review found deferred items, list them under **Follow-ups**.

## Decision guide

**Quick self-review is enough when:**

- Diff is small and localized
- Changes are docs-only or config-only
- User asked for a fast wrap-up

**Do a thorough self-review when:**

- Multiple modules touched (`buddy_tools/`, entry point, scripts)
- Speech-to-speech integration or tool registration changed
- User asks for careful review before opening the PR

## Troubleshooting

| Problem | Action |
|---------|--------|
| No changes to commit | Report clean working tree; skip commit and PR unless user wants an empty PR |
| Issue number unknown | Ask user once; do not guess |
| Dirty working tree blocks branch switch | Stash, switch, pop; confirm with user if stash conflicts |
| Branch already has an open PR | Push new commits to the same branch; mention existing PR in summary |
| `gh` not authenticated | Report; provide manual PR link instructions |
| Push rejected (non-fast-forward) | Pull/rebase from remote; ask user if history is unclear |
| Test suite fails | Fix in-scope failures and re-run; ask user if failures seem unrelated |
| No test suite yet | Document manual verification in PR test plan |

## Example

User agrees to wrap-up after implementing issue #8 on `main` with uncommitted changes.

1. `git status` → 2 files changed under `buddy_tools/`, 1 test file added
2. Create `issue/8-fix-memory-append` from `main`
3. Quick self-review of diff → no issues
4. `python -m pytest tests/ -x` → all passed
5. Stage, commit with message explaining the memory append fix
6. Push and `gh pr create --base main` with test plan noting pytest passed
7. Post wrap-up summary with PR link and test result
