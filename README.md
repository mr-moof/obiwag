# ObiWag AI Agent Framework

**v0.69.92**

Obi Wag coordinates Claude Code and Codex through a ten-phase coding workflow, with source verification, review, testing, and a release gate.

This is a standalone, GitHub-oriented distribution. It ships framework code, generic policies, and synthetic tests. Runtime memories, session transcripts, personal profiles, project-specific integrations, and credentials are not included.

## Current capabilities

- Ten phases: Discovery, Author, Simplify, Review, Integrate, Re-review, README, README Review, Release Gate, and Learning.
- Structured handoffs with bounded context and verified source references.
- Deterministic transitions based on phase signals and recorded evidence.
- Early routine/strong routing for Discovery and Author.
- Conditional Learning and concurrent independent reads.
- Bounded native phase execution, durable peer review, autonomous recovery, and collision-aware deployment.
- GitHub issue, pull-request, workflow, and prerequisite-probe integrations.

See [agent efficiency](docs/agent-efficiency.md), the [phase contract](phases/README.md), and [deployment](DEPLOYMENT.md).

## Quick start

Install Python, PowerShell, Git, and the CLI for your chosen assistant. GitHub operations also use `gh`.

```powershell
git clone https://github.com/mr-moof/obiwag.git
Set-Location obiwag
./tools/deploy.ps1 -DryRun
./tools/deploy.ps1
```

Review deployment targets first. The default tools directory is `~/.obi-tools`; `OBI_HOME` overrides it. Deployment sets `OBIWAG_SOURCE` to the checkout. Claude and Codex configuration use their respective user directories.

The example under `users/user/settings.json` is a template. Create your own untracked local configuration and replace `YOUR_USERNAME` before using it. Do not publish populated personal settings.

For Codex, the entry aliases are `obi`, `obi-auto`, `obi-auto-max`, `review`, and `release`. Read [Codex setup](platforms/codex/README.md) for profile configuration. Available model identifiers depend on your CLI account; the routing table is in `phases/phase-table.json`.

## Verification

Python tests require `pytest`; PowerShell tests require Pester 5.

```powershell
python -m pytest hooks/tests tools/tests tools/peer_review/tests --import-mode=importlib -q
./tools/run-tests.ps1 -PowerShellOnly
```

Tests use synthetic data. Do not run tests against an installed runtime containing private memories.

## Publishing safely

```powershell
python tools/public_safety.py --repo . --staged
python tools/public_safety.py --repo . --ref HEAD
```

The checker reads Git blobs, rejects runtime state, credential files, binary artifacts, private endpoints, and common secret literals, and reports only paths and rule names. Use `--denylist-file` with a private JSON array of terms kept outside the checkout. Also run a dedicated secret scanner and inspect commit metadata and outgoing history before pushing. These checks supplement manual review.

Peer review uses `OBI_TRUSTED_ROOT` (default `~/source`) to classify standing authorization. Other paths require the authorization handling described in [peer-review policy](policies/peer-review.md).

See [CHANGELOG](CHANGELOG.md) and [LICENSE](LICENSE).
