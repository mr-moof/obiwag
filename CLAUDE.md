# Obi Wag source repository

The global Claude contract is `platforms/claude-code/CLAUDE.global.md`. Codex instructions originate in `platforms/codex/AGENTS.md` and are copied to root `AGENTS.md` during deployment.

- Follow the ten-phase lifecycle in `phases/README.md` and its current phase table.
- Treat changes to orchestration, policies, hooks, and schemas as changes to the framework itself. Keep them within the user's authorized scope.
- Read evidence before editing; never invent APIs or schemas.
- Runtime memory and private settings are local data and must never be published.
- Run `tools/run-tests.ps1` for the appropriate test suites. Source contracts use `policies/`; deployed Claude contracts use the `docs/policies/` mirror.
- Deployment uses `OBI_HOME`, defaulting to `~/.obi-tools`. It records the checkout in `OBIWAG_SOURCE`.
- Use `tools/bump-version.ps1` to update version banners and the changelog, and `tools/run-grep-gates.ps1 -VersionDrift` to check parity.
- Before public publication, audit the exact staged tree with `tools/public_safety.py`, run a secret scanner, and inspect commit metadata and outgoing history.
