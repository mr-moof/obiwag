---
topic: github
confidence: 0.85
last_updated: 2026-02-18
match_keywords:
  - github
  - gh
  - pull request
  - pipeline
  - actions
  - ci/cd
sources:
  - path: .obi/patterns/github.md
    priority: 1
    description: GitHub operations and configuration patterns
---

# GitHub Operations Pattern

## Environment Details

- **Host**: `github.com`
- **Protocol**: HTTPS (or SSH if you've configured keys)
- **CLI**: the GitHub CLI (`gh`), authenticated via `gh auth login`

## CLI Configuration

### gh CLI
Authenticate once; `gh` then targets github.com by default:

```bash
gh auth login                      # one-time auth (browser or token)
gh repo create <name> --private    # create a new repo
gh issue list                      # list issues
gh pr create                       # open a pull request
```

### Git Remote
Use HTTPS URLs (or SSH if you've set up keys):

```bash
git remote add origin https://github.com/<username>/<repo>.git
```

## Commit Email

GitHub does not enforce a committer-email domain. Use the email associated with
your GitHub account (or your GitHub `noreply` address) so commits attribute
correctly:

```bash
git config user.email "you@example.com"            # current repo
git config --global user.email "you@example.com"   # all repos
git commit --amend --reset-author --no-edit         # fix the last commit's author
```

## Common Operations

### Create and Push a New Repo
```bash
git init
git add .
git commit -m "Initial commit"
gh repo create <name> --private --source . --remote origin --push
```

### Clone an Existing Repo
```bash
git clone https://github.com/<username>/<repo>.git
# or: gh repo clone <username>/<repo>
```

## CI (GitHub Actions)

- Workflows live in `.github/workflows/*.yml`.
- Runners are Linux by default — use `/` path separators; the filesystem is case-sensitive (`LICENSE` ≠ `license`).
- Watch a run with `gh run watch`; inspect failures with `gh run view --log-failed`.
