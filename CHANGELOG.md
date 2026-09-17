# Changelog

## 0.69.92 - 2026-09-17

- Add bounded phase handoffs, deterministic transitions, early routing, conditional Learning, and concurrent independent reads.
- Add native phase timelines, autonomous recovery, durable peer review, and collision-aware deployment.
- Retain GitHub integrations and user-local installation defaults.
- Remove shipped runtime state; isolate grounding tests with synthetic fixtures.
- Add a Git-tree publication audit for secrets, local artifacts, private endpoints, and an optional external denylist.

## v0.69.58 — independent baseline

Generic, vendor-neutral baseline of the Obi Wag AI agent framework, forked for
standalone use. Prior project history is not carried over.

- 10-phase, lane-first workflow (Claude Code + Codex)
- Zero-hallucination policy, three-strike rule, vendor-wrapper boundary
- Self-healing memory (SessionStart / PostToolUse / Stop hooks, calibration)
- Swarm mode; autonomous (`/obi-auto`) and rigor=max (`/obi-auto-max`) modes
- Forge integration targets GitHub (`gh` CLI, GitHub Actions)
