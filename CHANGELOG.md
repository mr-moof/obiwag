# Changelog

## v0.69.58 — independent baseline

Generic, vendor-neutral baseline of the Obi Wag AI agent framework, forked for
standalone use. Prior project history is not carried over.

- 10-phase, lane-first workflow (Claude Code + Codex)
- Zero-hallucination policy, three-strike rule, vendor-wrapper boundary
- Self-healing memory (SessionStart / PostToolUse / Stop hooks, calibration)
- Swarm mode; autonomous (`/obi-auto`) and rigor=max (`/obi-auto-max`) modes
- Forge integration targets GitHub (`gh` CLI, GitHub Actions)
