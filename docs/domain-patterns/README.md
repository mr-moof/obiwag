# Vendor / Technology API Wrappers Index

> **Version:** 1.0

This directory documents **proven** vendor/technology API patterns used in this
repo. It ships empty — the framework is domain-agnostic — and you populate it as
you integrate.

## Purpose

All vendor API interactions must go through documented wrappers. This directory
is the reference for:

- Available integrations
- Proven API patterns
- Parameter documentation
- Error handling patterns

## Documented Integrations

_None yet._ Add a row here when you create a `docs/domain-patterns/<name>.md`:

| Integration | Status | Documentation |
|-------------|--------|---------------|
| _(example)_ ExampleCloud | _template_ | `example-cloud.md` |

## Documentation Format

Each wrapper doc should include:

### 1. Authentication
- How to authenticate
- Token / key / certificate management

### 2. Available Operations
- Supported API calls
- Required + optional parameters
- Return types

### 3. Working Examples
- Code snippets from existing modules
- Error handling patterns
- Retry logic

### 4. Common Errors
- Known error codes
- Troubleshooting steps

## Usage Rules

1. **Never bypass wrappers** — business logic calls wrappers, not APIs directly
2. **Verify before using** — check this documentation exists before coding
3. **Update after discovery** — if you find new patterns, document them
4. **Evidence required** — no documentation = no usage

## Adding Documentation

1. Create `docs/domain-patterns/<name>.md`
2. Document the authentication method
3. List available operations with parameters
4. Include working code examples from the repo
5. Document common errors
6. Add a row to the table above

## Zero Hallucination Enforcement

If an integration is not documented here:

1. Output `MISSING SOURCE: [vendor] [operation]`
2. Stop implementation
3. Request documentation or evidence
4. Only proceed after documentation is added

For a catalog of **queryable data sources** (SQL servers, MCP servers, CLI tools)
and what questions they answer, see `docs/data-sources.md`. This directory
documents *how to use vendor APIs*; the data-source catalog documents *what data
is available and where*.

See `docs/policies/vendor-rules.md` and `docs/policies/zero-hallucination.md` for full policy.
