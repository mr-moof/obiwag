# Git and GitHub Setup Guide

This guide covers installing Git and configuring credentials so you can clone, pull,
and push to GitHub over HTTPS.

---

## Prerequisites

- A GitHub account with access to the repository you want to clone.
- Network access to https://github.com.

---

## Step 1: Install Git

Choose one of these options:

### Option A: Download the installer (recommended)

Download and run the installer from https://git-scm.com/download/win (Windows) or
https://git-scm.com/downloads (macOS/Linux). The Windows installer includes Git
Credential Manager, which stores your GitHub credentials after the first login.

### Option B: Via a package manager

```powershell
# Windows (Chocolatey)
choco install git -y

# Windows (winget)
winget install Git.Git
```

```bash
# macOS (Homebrew)
brew install git

# Debian/Ubuntu
sudo apt-get install git
```

---

## Step 2: Configure Git Identity

After installing Git, **restart your terminal** and set the name and email that will
appear on your commits:

```powershell
git config --global user.name "Your Name"
git config --global user.email "you@example.com"
```

---

## Step 3: Authenticate to GitHub

You have two ways to authenticate Git over HTTPS. Pick one.

### Option A: GitHub CLI (recommended)

Install the GitHub CLI (`gh`) from https://cli.github.com, then run:

```powershell
gh auth login
```

Follow the prompts (choose GitHub.com, HTTPS, and authenticate in the browser or with a
token). `gh auth login` also configures Git to use `gh` as the credential helper, so
`git clone`/`git push` over HTTPS just work afterward.

### Option B: Personal Access Token (PAT)

If you prefer not to use `gh`, create a GitHub Personal Access Token and use it as your
HTTPS password:

1. Go to https://github.com/settings/tokens and create a token (a fine-grained token
   scoped to the repositories you need, or a classic token with the `repo` scope).
2. Set an expiration and copy the token immediately — you won't see it again.
3. When Git prompts for a password during `git clone`/`git push`, paste the token
   instead of your account password (your GitHub username goes in the username field).

> **Tip:** Store the token in a password manager. Git Credential Manager (bundled with
> Git for Windows) saves it after the first successful login, so you only enter it once.

---

## Step 4: Clone a Repository

```powershell
git clone https://github.com/user/obiwag-agents.git
```

If prompted for credentials, supply your GitHub username and either the token (Option B)
or let `gh`/Git Credential Manager handle it (Option A).

---

## Verifying Your Setup

```powershell
# Check Git is installed
git --version

# Check your identity is configured
git config --global user.name
git config --global user.email

# (If using gh) confirm you're authenticated
gh auth status
```

If `git clone` succeeds, you're all set.

---

## Troubleshooting

### "Authentication failed" error

- Confirm you're authenticated: `gh auth status` (Option A), or that your PAT is valid
  and not expired (Option B).
- For a PAT, make sure it has access to the repository (the `repo` scope for classic
  tokens, or repository access for fine-grained tokens).

### Git keeps asking for credentials

Enable the credential helper so credentials are cached after the first login:

```powershell
# Windows
git config --global credential.helper manager

# macOS
git config --global credential.helper osxkeychain

# Linux (cache in memory for a session)
git config --global credential.helper cache
```

### Need to change saved credentials

```powershell
# Windows: remove the stored github.com credential
cmdkey /delete:git:https://github.com

# Or, if using gh, re-authenticate
gh auth logout
gh auth login
```

Then run `git clone` or `git pull` again to re-enter credentials.

---

## Revoking a Compromised Token

If a PAT is exposed:

1. Go to https://github.com/settings/tokens.
2. Delete (revoke) the compromised token.
3. Create a new token and update your saved credentials (see "Need to change saved
   credentials" above).
