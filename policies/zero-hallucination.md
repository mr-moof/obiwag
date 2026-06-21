# Zero Hallucination Policy

> **Version:** 1.0 | **Last Updated:** 2026-02-02

## Purpose

Prevent AI from inventing APIs, endpoints, parameters, or behaviors that don't exist.
Every technical claim must be backed by verifiable evidence.

## The Rule

**Never invent APIs, endpoints, cmdlets, parameters, or behaviors — whether vendor-specific or technology-specific.**

If you cannot prove it exists in the repo, you cannot use it.

## What Counts as Hallucination

| Type | Example | Why It's Hallucination |
|------|---------|------------------------|
| Invented endpoint | `POST /api/v2/widgets/sync` | No evidence this endpoint exists |
| Guessed parameter | `Get-VM -IncludeSnapshots` | Parameter not in documented cmdlet |
| Assumed behavior | "This returns a list" | No evidence of return type |
| Fabricated cmdlet | `Invoke-ServiceSync` | Cmdlet doesn't exist |
| Wrong method | Using PUT when API uses PATCH | API contract violation |

## Evidence Requirements

### Acceptable Evidence

1. **Code in this repo** - Working example in existing module
2. **Wrapper/reference documentation** - Documented in `docs/domain-patterns/` or `docs/`
3. **Test output** - Actual response from running tests
4. **Official docs in repo** - Vendor documentation committed to repo

### NOT Acceptable Evidence

- "I know this API exists" (training data is not evidence)
- "The documentation says..." (which documentation? show it)
- "It should work..." (should is not evidence)
- "Other tools use this..." (show evidence in THIS repo)

## When Evidence Is Missing

### Signal

Output: `MISSING SOURCE: [vendor/system] [API/endpoint/function]`

### Then

1. **STOP** - Do not proceed without evidence
2. **Document** - What you were trying to use
3. **Request** - Ask the user for documentation or working example
4. **Wait** - Do not guess or invent alternatives

### Example

```
MISSING SOURCE: ticketing-system change-request table schema

I need to create a change request but cannot find evidence of:
- Required fields for the change-request table
- Valid values for state field
- Approval workflow fields

Please provide:
- Link to the vendor's documentation, OR
- Working example in repo, OR
- Manual for me to follow
```

## Verification Process

Before writing any code that interacts with external systems:

1. **Search repo** - Use Grep/Glob to find existing usage
2. **Check wrappers/references** - Look in `docs/domain-patterns/` and `docs/`
3. **Find tests** - Locate tests that exercise the integration
4. **Verify patterns** - Match your usage to documented patterns

## Common Traps

### "I'm confident about this API"
Your training data may be outdated or wrong. Find repo evidence.

### "The vendor documentation says..."
Is that documentation in this repo? Can you show it? If not, it's not evidence.

### "This is a standard REST pattern"
Standards vary. This specific API may not follow standards. Find evidence.

### "I'll test it and see"
Testing guesses wastes time. Find evidence first, then implement.

## Integration with Review

The `/review` phase specifically checks for hallucination:

```
### Anti-Hallucination Check
- [ ] All vendor/technology APIs verified in repo evidence
- [ ] Wrapper boundary respected
- [ ] No invented endpoints or parameters
- [ ] No direct vendor SDK calls from business logic
```

Any unchecked item = Review FAIL.

## Consequences

Hallucination in code leads to:
- Runtime errors (API doesn't exist)
- Silent failures (wrong parameters ignored)
- Security issues (assumptions about auth/encryption)
- Technical debt (code that works by accident)

## The Bottom Line

**If you can't show evidence, you can't use it.**

No exceptions. No "just this once." No "I'm pretty sure."
