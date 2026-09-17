# Deployment

## Prerequisites

- Python with `pytest` for tests.
- PowerShell (Windows PowerShell 5.1 or PowerShell 7) and Pester 5 for PowerShell tests.
- Git, and the CLI for Claude Code or Codex.
- GitHub CLI (`gh`) for GitHub operations.

## Install from this repository

```powershell
git clone https://github.com/mr-moof/obiwag.git
Set-Location obiwag
./tools/deploy.ps1 -DryRun
./tools/deploy.ps1
```

Use `-ClaudeOnly` or `-CodexOnly` to select one platform. The dry run reports targets before writing. Existing managed-file edits are protected by manifest collision checks; inspect a collision instead of forcing deployment blindly.

Default targets are `~/.claude`, `~/.codex`, and `~/.obi-tools`. Set `OBI_HOME` explicitly to choose another tools directory. Deployment sets user-level `OBI_HOME` and `OBIWAG_SOURCE`; restart the assistant after installation so it inherits them. Keep separate environments for unrelated installations.

Claude settings are loaded from `users/<username>/settings.json` when present. The shipped `users/user/settings.json` contains placeholder paths. Copy it to your own local directory, replace `YOUR_USERNAME`, and keep that populated file out of Git. No credentials or provider login state are supplied.

Codex instructions originate in `platforms/codex/AGENTS.md`; deployment copies them to root `AGENTS.md`. It also writes project hooks and copies the optional Obi profile. Follow [Codex setup](platforms/codex/README.md) to activate the profile.

## Validate

```powershell
./tools/config-guardian.ps1 -Quick -CheckOnly
python tools/healthcheck.py --quick
python -m pytest hooks/tests tools/tests tools/peer_review/tests --import-mode=importlib -q
./tools/run-tests.ps1 -PowerShellOnly
```

Health checks inspect the selected installation. Run tests in an isolated environment when another installation contains private memories.

## Runtime data and publication

Sessions create `.obi` state locally. It is ignored by Git and must not be added to a public release. The deployment does not include prepopulated project knowledge.

Run `python tools/public_safety.py --repo . --staged`, a dedicated secret scanner, and a manual review before publishing. Keep private audit reports and denylists outside the repository.

## Uninstall

Run `./tools/uninstall.ps1 -DryRun` to inspect managed removals, then run without `-DryRun` when ready. Check `OBI_HOME` first so the operation targets the intended installation.
