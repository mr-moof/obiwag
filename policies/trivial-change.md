# Trivial Change Policy

> **Version:** 1.1 | **Last Updated:** 2026-06-18 (OPT-18 lane-first)

## Purpose

Skip nearly all phases for trivial changes that carry minimal risk.
Trivial Lane is stricter than Express Lane - for truly inconsequential edits.

## Threshold

**5 lines or fewer** AND affects only:
- Comments (single-line `//`, `#`, `--` or block comments)
- Whitespace (indentation, blank lines)
- Typos in strings, variable names, or documentation
- Formatting (line breaks, trailing commas)

## Detection

After Author, `tools/classify-lane.ps1 -Base HEAD~1` reports the code lines changed and an advisory
`appears_comment_only` flag. Trivial Lane applies when BOTH hold (the orchestrator confirms the
second, semantic, criterion):
1. Total insertions + deletions <= 5 lines
2. All changes are comments, whitespace, typos, or formatting

## What Gets Skipped

Lane-first (OPT-18): the `trivial` lane in `phases/phase-table.json` declares the TOTAL phase list
`[2, 9]` — **only Author (2) and Release Gate (9) run**; every other phase is absent. The `lanes`
structure is the canonical source; this policy holds only the threshold + the "what does NOT
qualify" exclusions that classify a change INTO the trivial lane.

## What Does NOT Qualify

Trivial Lane does NOT apply when:

1. **Any functional code changes** - Even 1 line of logic is not trivial
2. **Test file changes** - Tests affect behavior verification
3. **Configuration changes** - `.json`, `.yaml`, `.env` files affect runtime
4. **Public API changes** - Even comment changes on public interfaces
5. **Security-related files** - Auth, encryption, credentials
6. **Build/CI files** - Dockerfiles, CI configs, package manifests

## Examples

### Qualifies for Trivial Lane

```diff
- # This function calcualtes the hash
+ # This function calculates the hash
```

```diff
- $result = Get-Something   # old comment
+ $result = Get-Something   # clearer comment
```

```diff
  function doThing() {
-
      const x = 1
  }
```

### Does NOT Qualify

```diff
- return x + 1
+ return x + 2    # This is a logic change
```

```diff
- // TODO: implement
+ const result = compute()  # Functional change
```

## Signals

When Trivial Lane applies, output:
```
TRIVIAL LANE: [N] lines of comments/whitespace - Author → Release Gate only
```

When Trivial Lane doesn't apply but Express Lane does:
```
EXPRESS LANE: [N] lines changed
```

## Comparison

| Criteria | Normal | Express Lane | Trivial Lane |
|----------|--------|--------------|--------------|
| Line threshold | N/A | <25 lines | <=5 lines |
| Change type | Any | Any | Comments/whitespace only |
| Phases run | 10 | 8 | 2 |
| Risk level | High | Medium | Minimal |

---

*See `policies/express-lane.md` (deployed to `docs/policies/express-lane.md`) for Express Lane rules.*
