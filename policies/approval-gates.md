# Human Approval Gates

> **Version:** 1.0 | **Last Updated:** 2026-05-22

## Purpose

Define which changes require the user approval before proceeding vs. which proceed
autonomously. This is the **approval taxonomy** — a distinct concept from
`hard-stop-conditions.md`, which documents enforcement triggers that halt
the workflow regardless of approval state.

| Document | Scope |
|---|---|
| `policies/approval-gates.md` (this file) | When to ASK before acting |
| `policies/hard-stop-conditions.md` | When to STOP because a rule was violated |

A change can land in both buckets: e.g. modifying an auth wrapper without
approval triggers BOTH "Security boundary violation" (HARD STOP) and
"Security-sensitive change" (requires approval).

## Requires the user Approval

The following classes of change MUST be confirmed with the user before they ship:

- **Security-sensitive changes** — anything touching auth, credentials,
  encryption, certificates, secret handling, or session token storage.
- **Destructive operations** — file deletion that isn't a manifest-tracked
  cleanup, `git push --force`, `git reset --hard` of upstream-tracked refs,
  data removal from any persistence layer.
- **Policy / workflow edits** — modifications to files under `policies/`,
  `orchestration/`, `phases/`, or the hook contract under `hooks/`.
- **Memory-schema changes** — alterations to `auto-memory/memory-schema.md`
  or the structural shape of files under `~/.claude/projects/<proj>/memory/`.
- **New vendor integrations** — adding support for a new external API or
  on-prem appliance (a cloud provider, a hypervisor, a ticketing system, etc.).
- **Breaking changes** — any change that alters an existing public contract
  (slash command name/args, hook output schema, manifest format) such that
  prior consumers would need to update.

## Proceed Autonomously

The following classes DO NOT require approval — proceed and report:

- Documentation fixes, typos, clarifications, reformatting.
- Bug fixes inside existing commands where the behavior contract is
  unchanged.
- Adding tests, refactors that don't change observable behavior, session
  state updates, discovery / research artifacts.
- Linter and dependency-lockfile bumps within a major version.
- README, gotchas, and pattern-library additions that document existing
  behavior.

## When Uncertain

- **First encounter with an ambiguity**: ask the user. The cost of one question
  is much smaller than rework after the fact.
- **Established pattern**: proceed. If you've already asked once for a class
  of change and the user's answer was "go", apply the same answer to comparable
  later instances.
- **Multiple simultaneous changes**: split the work — the approval-gated
  changes go on a branch with a description, the autonomous ones land
  directly on master.

## Why this is separate from hard-stop-conditions

`policies/hard-stop-conditions.md` defines triggers for IMMEDIATE workflow
halt: hallucinated APIs, missing test evidence, security boundary
violations. Those are enforcement points that block bad actions from
landing at all.

`approval-gates.md` (this file) defines the conversational contract: when
should I pause and ask the user before taking an action that's otherwise valid?
The same change can sit in both lists (a security boundary touch is both a
HARD STOP if attempted without approval AND a class that requires approval
before being attempted) — but most approval-gated changes are not HARD
STOPs, just changes whose blast radius is too big to land without a
second pair of eyes.

## See also

- `policies/hard-stop-conditions.md` — workflow-halt enforcement triggers
- `policies/three-strike-rule.md` — failure-loop break protocol
- `policies/vendor-rules.md` — zero-hallucination vendor contract
