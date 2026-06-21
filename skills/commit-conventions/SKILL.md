---
name: commit-conventions
description: Git commit conventions for this project. Auto-apply when creating commits, branches, or pull requests.
user-invocable: false
---

# Commit Conventions

## Commit Message Format

Use **conventional commits**:

```
<type>: <description>
```

| Type | Use When |
|------|----------|
| `feat:` | New feature or capability |
| `fix:` | Bug fix |
| `docs:` | Documentation only |
| `refactor:` | Code restructuring, no behavior change |
| `test:` | Adding or updating tests |
| `chore:` | Build, CI, dependency updates |

## Commit Email

Commits use your configured git email — set it to the address associated with
your forge account so commits attribute correctly:

```bash
git -c user.email=you@example.com commit -m "feat: add new feature"
```

## Issue References

- Use `(74)` NOT `(#74)` — a leading `#` in commit messages can trip Claude Code's newline safety prompt when commits are made through Bash heredocs.
- Example: `fix: resolve auth timeout (74)`

## Branch Naming

- Use kebab-case: `feature-name`, `fix-auth-timeout`
- Include the issue number when applicable: `74-fix-auth-timeout`

## Branching Strategy

| Situation | Strategy |
|-----------|----------|
| Solo work on a small / low-risk change | Commit directly to the default branch |
| Parallel work, or a change risky enough to want review | Feature branch + pull request |
| Shared / team repos | Feature branch + pull request |

When unsure, default to **feature branch + PR**.

## GitHub CLI

- Use `gh` for forge operations (issues, PRs, runs).
- PR creation: `gh pr create --title "..." --body "..."`
- Issue references: `gh issue view <number>`

## Swarm Worker Commits

Swarm workers may commit with a placeholder email such as `swarm@obiwag.local`.
After merge, rewrite those commits to your real email:
```bash
git filter-branch --env-filter '...' -- HEAD~N..HEAD
```
to `you@example.com`.
