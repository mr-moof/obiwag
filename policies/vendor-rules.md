# Vendor and Technology Rules

> **Version:** 1.0

## Purpose

Define rules for interacting with third-party vendor APIs, SDKs, services, and
technology-specific patterns. Prevent hallucination by requiring evidence before
any vendor or technology integration.

## Core Principle

**Never call a vendor API directly from business logic.**

All vendor interactions must go through documented wrappers with verified API patterns.

## Wrapper Boundary

```
Business Logic → Wrapper → Vendor API
     ✓              ✓           ✗
  You write     Verified    Never touch
                 patterns     directly
```

### What You Can Do
- Call wrapper functions documented in `docs/domain-patterns/`
- Use patterns shown in existing working modules
- Request new wrapper functions with evidence

### What You Cannot Do
- Invent API endpoints
- Guess parameter names
- Call vendor SDKs directly from business code
- Assume API behavior based on documentation you haven't verified in repo

## Evidence Requirements

Before using any vendor integration:

| Requirement | Where to Find |
|-------------|---------------|
| Wrapper exists | `docs/domain-patterns/[vendor].md` |
| API documented | Wrapper documentation or inline comments |
| Working example | Existing module using the wrapper |

If ANY of these are missing, output `MISSING SOURCE: [vendor] [function]` and STOP.

## Supported Integrations

This framework ships with **no** vendor integrations preconfigured — it stays
domain-agnostic on purpose. When you integrate a vendor or technology, document
it under `docs/domain-patterns/<name>.md` and it becomes a supported,
evidence-backed integration for this repo.

## Adding New Integration

1. **Discovery Phase:** Find official vendor documentation
2. **Wrapper Creation:** Create wrapper module with documented patterns
3. **Evidence Documentation:** Add to `docs/domain-patterns/`
4. **Review Gate:** Must pass review with evidence

## Common Violations

| Violation | Why It's Wrong | Correct Approach |
|-----------|----------------|------------------|
| "The API should have this endpoint" | Assumptions cause failures | Find evidence or ask |
| "This parameter probably works" | Untested parameters break | Use documented parameters |
| "I'll call the SDK directly" | Breaks wrapper boundary | Use wrapper function |
| "Other tools do it this way" | Different tools, different APIs | Find evidence in THIS repo |

## Writing a Vendor Note (template)

A `docs/domain-patterns/<vendor>.md` should capture only what you have *verified*:

- **Auth:** how the wrapper authenticates (token, key, certificate) — no guesses.
- **Operations:** the exact wrapper functions/cmdlets, their real parameters, and
  the return shapes you have observed.
- **Gotchas:** non-obvious behavior proven in practice (e.g. "this cmdlet returns
  the property under name `Availability`, not `Presence`"; "the SDK emits benign
  warnings — don't convert them to terminating errors").

Document behavior you have seen, never behavior you assume.

## Verification Checklist

Before any vendor API call:

- [ ] Wrapper function exists and is documented
- [ ] Parameters match documented signature
- [ ] Error handling follows wrapper patterns
- [ ] Working example exists in repo
- [ ] Test covers vendor interaction (may be mocked)
