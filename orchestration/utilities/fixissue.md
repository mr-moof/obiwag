---
description: Fix a GitHub issue end-to-end. Read, implement, test, commit, close.
allowed-tools: Read, Glob, Grep, Edit, Write, Bash
---

# Fix Issue - GitHub Issue Resolution

Resolve a GitHub issue through the full lifecycle: read, implement, verify, commit, close.

## Input

`$ARGUMENTS` contains a GitHub issue number (e.g., "42").

## Procedure

### Step 1: Read the Issue

```bash
gh issue view $ARGUMENTS
```

Parse the title, description, acceptance criteria, and labels. Identify which files are likely affected.

### Step 2: Verify Current State

Before implementing, check whether the issue still applies:
- Read the files mentioned in the issue
- Check if the described problem still exists
- If already resolved, note it on the issue and close it

### Step 3: Implement the Fix

- Use Read to examine all relevant files first
- Use Edit to make changes (never Bash for file I/O)
- Follow existing code style and patterns
- Keep changes minimal and focused on the issue

### Step 4: Verify the Fix

Run the `/verify` skill to check the fix. At minimum:
- Show the changed file contents as evidence
- Run any relevant tests
- Confirm acceptance criteria are met

If verification fails, do not claim success. Report what failed and adjust.

### Step 5: Commit and Push

Choose the branching strategy for the change (see `skills/commit-conventions`):
- **Small / low-risk:** commit directly to the default branch
- **Risky or parallel work:** feature branch + pull request

Commit with conventional commit format:
```bash
git -c user.email=you@example.com commit -m "fix: [description from issue]"
```

Push to the appropriate branch.

### Step 6: Close the Issue

```bash
gh issue close $ARGUMENTS
```

If the fix warrants a closing comment, add one first:
```bash
gh issue comment $ARGUMENTS --body "Fixed in [commit hash]. [brief summary of what changed]."
```
