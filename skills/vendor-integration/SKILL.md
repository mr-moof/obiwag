---
name: vendor-integration
description: Vendor API integration guidelines. Enforces wrapper boundaries and zero-hallucination policy when integrating any third-party API, SDK, or service.
user-invocable: false
---

# Vendor Integration Guidelines

## Core Rule

**Never call a vendor API directly from business logic.** All vendor interactions must go through documented wrappers.

```
Business Logic → Wrapper → Vendor API
     OK           OK         NEVER directly
```

## Before Any Vendor API Call

1. **Search repo** for existing wrapper usage (`Grep`/`Glob`)
2. **Check `docs/domain-patterns/`** for documented patterns
3. **Find working examples** in existing modules
4. **Verify parameters** match documented signatures

## If Evidence Is Missing

Output `MISSING SOURCE: [vendor] [API/function]` and **STOP**.

Do not:
- Invent API endpoints
- Guess parameter names
- Call vendor SDKs directly from business code
- Assume API behavior from training data

## Common Pitfalls (generic)

These apply to most vendor SDKs/APIs regardless of vendor — verify the specifics
before relying on them:

- **Noisy-but-benign errors.** Some SDKs emit warnings or non-fatal schema errors
  mid-pipeline. Don't blanket-wrap calls in `try/catch` / `-ErrorAction Stop` if
  that turns a benign warning into a terminating error and kills the pipeline.
  Suppress noise (`2>$null`, `-WarningAction SilentlyContinue`) while letting data
  flow — but only after you've *verified* the error is benign.
- **Property-name drift.** Return objects often expose a value under a name you
  didn't expect (e.g. an availability flag named `Availability`, not `Presence`).
  Confirm the real property name against live output.
- **Field limiting / pagination.** REST APIs degrade or truncate without explicit
  field selection and pagination. Request only the fields you need and page
  through large result sets.
- **Connection lifecycle.** Some SDKs require an explicit (re)connect before calls
  and leak state across iterations. Follow the wrapper's documented
  connect/disconnect pattern.

Capture the specifics you discover for a given vendor in
`docs/domain-patterns/<vendor>.md` — that file, not this skill, is the source of
truth for vendor-specific behavior.

## Verification Checklist

- [ ] Wrapper function exists and is documented
- [ ] Parameters match documented signature
- [ ] Error handling follows wrapper patterns
- [ ] Working example exists in repo
- [ ] Test covers vendor interaction (may be mocked)

## Full Policies

- `docs/policies/zero-hallucination.md` - Evidence requirements
- `docs/policies/vendor-rules.md` - Wrapper boundary rules
