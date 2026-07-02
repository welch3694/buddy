---
name: issue
description: Full GitHub issue lifecycle using gh CLI — quick capture for bugs/enhancements plus view, edit, comment, close, list, search. Use when user asks to file, log, or track an issue in welch3694/buddy.
---

# GitHub Issue Lifecycle

## Purpose

Manage the complete GitHub issue lifecycle for `welch3694/buddy` using the `gh` CLI. Covers creation, viewing, editing, commenting, closing, listing, and searching issues.

## Repository

- **Owner/Repo**: `welch3694/buddy`
- ** gh alias**: `gh` (v2.63.0+)
- **Authenticated**: Yes (github_pat, repo scope)

## Title Conventions

Prefix with one of:

| Type | Prefix |
|------|--------|
| Bug | `Bug: ` |
| Enhancement | `Enhancement: ` |
| Task | `Task: ` |
| Question | `Question: ` |

Keep titles under 80 characters. Be specific — avoid vague titles like "Fix things" or "Update code."

## Label Conventions

| Category | Labels |
|----------|--------|
| Type | `bug`, `enhancement`, `task`, `question` |
| Priority | `priority:high`, `priority:medium`, `priority:low` |
| Component | `speech`, `tools`, `memory`, `scripts`, `tests`, `deps` |
| Status | `in-progress`, `needs-review`, `blocked` |

When creating issues, always include:
- Type label (required)
- At least one priority label
- Component label(s) if identifiable

## Body Templates

### Bug Report

```markdown
## Summary
<clear problem statement, what's broken, impact>

## Environment
- OS: Windows 11 Pro
- Python: 3.12+
- Branch: <branch>
- llama-server / CUDA version: <if relevant>

## Steps To Reproduce
1. ...
2. ...
3. ...

## Expected Behavior
<what should happen>

## Actual Behavior
<what happens now>

## Evidence
- Logs: `<paste or attach>`
- Screenshots: `<attach if available>`
- Related files: `<list files>`

## Acceptance Criteria
- [ ] <criterion 1>
- [ ] <criterion 2>

## Notes
<optional implementation hints, constraints, links>
```

### Enhancement / Task

```markdown
## Summary
<what should be built or changed>

## Motivation
<why this matters, what problem it solves>

## Acceptance Criteria
- [ ] <criterion 1>
- [ ] <criterion 2>

## Implementation Notes
<optional: approach, constraints, related files>
```

### Question

```markdown
## Summary
<what you want to understand>

## Context
<what you've tried, what you know so far>

## Question
<clear, specific question>
```

## Quick Capture

**Default mode** when the user gives a short prompt (`file an issue`, `log this`, or a one-liner bug/enhancement idea).

1. **Do not interview** — skip follow-up questions unless the issue type is genuinely ambiguous.
2. **Still check duplicates** — run a quick keyword search before creating.
3. **Minimal body** — use only `Summary` and `Acceptance Criteria` (1–2 checkboxes).
4. **Infer defaults**:
   - Type: `bug` if something is broken; otherwise `enhancement` (use `task` or `question` only when obvious)
   - Priority: `priority:medium`
   - Component: add when identifiable from context, open files, or recent work
5. **Enrich from context** — pull in related files, branch, or session details when available; do not ask the user to repeat them.

Quick capture body template:

```markdown
## Summary
<user's words, expanded slightly for clarity>

## Acceptance Criteria
- [ ] <single clear done condition>
```

Example prompts: `File an issue: memory append fails on empty notes`, `Log enhancement — add screen capture tool`.

## Creation Workflow

### 1. Check for Duplicates

Before creating, search existing issues:

```bash
gh issue list --state all --label "<relevant-label>" --json number,title 2>&1
gh search issues "<keywords>" --json number,title 2>&1
```

If a matching issue exists, note its number and link to it rather than creating a duplicate.

### 2. Draft Title and Body

From user-provided details or discovered context, draft:
- Title following conventions
- Body using appropriate template
- Labels (type + priority + component)

### 3. Create Issue

```bash
gh issue create \
  --title "Bug: <description>" \
  --body "```markdown
## Summary
...
## Acceptance Criteria
- [ ] ...
```" \
  --label "bug,priority:high,tools"
```

For longer bodies, write to the body file and use `--body-file`:

```bash
gh issue create --body-file .cursor/tmp/issue-body.md --label "enhancement,priority:medium,tools"
```

**Body file path:** always use `.cursor/tmp/issue-body.md`. Overwrite it as needed for each issue; it is gitignored and persists for reuse.

### 4. Report Result

After creation, report:
- Issue number and title
- URL
- Labels applied
- One-line summary of acceptance criteria

## Viewing Issues

```bash
# View a specific issue
gh issue view <NUMBER>

# View with full discussion
gh issue view <NUMBER> --comments
```

## Editing Issues

```bash
# Edit title
gh issue edit <NUMBER> --title "New title"

# Edit body (append)
gh issue edit <NUMBER> --body "```markdown
## Additional Notes
...
```" --append

# Add labels
gh issue edit <NUMBER> --add-label "in-progress,needs-review"

# Remove labels
gh issue edit <NUMBER> --remove-label "blocked"
```

## Commenting

```bash
# Add a comment
gh issue comment <NUMBER> --body "```markdown
## Analysis
...
```"

# View comments
gh issue view <NUMBER> --comments
```

## Closing Issues

```bash
# Close with reason
gh issue close <NUMBER> --comment "```markdown
## Resolved
Fixed in PR #<N>.
```"

# Reopen
gh issue reopen <NUMBER>
```

## Listing and Searching

```bash
# List issues (filter by state, label, author)
gh issue list --state open --label "bug"
gh issue list --state closed --author <user>
gh issue list --state all --json number,title,state,labels

# Search across issues and PRs
gh search issues "memory tool" --json number,title,state

# List open issues by priority
gh issue list --state open --label "priority:high"
```

## Autonomous Creation Triggers

Create an issue when you:
1. Discover a bug that needs tracking (not just a quick fix)
2. Identify a design decision that needs documentation
3. Find a code smell or technical debt item worth formalizing
4. Complete a feature that should have a tracking issue
5. User asks to "file an issue" or "log this"

Do NOT create an issue when:
- Fixing a trivial typo or obvious formatting issue
- The change is already captured in the current PR/branch
- User says "don't create an issue for this"

## Error Handling

| Error | Action |
|-------|--------|
| ` gh: command not found` | Report missing CLI, fall back to URL: `https://github.com/welch3694/buddy/issues/new` |
| Auth failure | Run `gh auth login` |
| Validation error | Show `gh` error, retry with corrected fields |
| Duplicate found | Report existing issue number and URL instead of creating |
| Create failed | `.cursor/tmp/issue-body.md` remains for retry or edit |

## Response Format

After any issue operation, report:
- **Issue #**: number
- **Title**: full title
- **URL**: full URL
- **State**: open/closed
- **Labels**: applied labels
- **Summary**: one-line description of what was done
