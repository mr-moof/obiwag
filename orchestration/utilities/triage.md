---
description: Incident triage. Accepts an incident description or ticket id, searches available knowledge sources, and generates a structured, evidence-grounded resolution post.
allowed-tools: Read, Glob, Grep, Bash
---

# Triage — Incident Knowledge Lookup

Triage an incident by searching the knowledge sources available to this project
(repo docs, and any knowledge-base / search MCP server you have configured — see
`docs/data-sources.md`). Generate a structured resolution post grounded in what
you find.

## Input

`$ARGUMENTS` contains either:
- A free-text incident description (e.g., "server not booting after firmware update")
- A ticket id from your tracker (e.g., "INC1234567", "BUG-4521")

## Procedure

### Step 1: Parse Input

If the input looks like a ticket id (a short prefixed/alphanumeric token), note it
for the output header. Otherwise, treat it as a symptom description.

### Step 2: Search Knowledge Sources

Search whatever knowledge sources are available — repo docs (`Grep`/`Glob` over
`docs/`) and any KB / search MCP tool documented in `docs/data-sources.md`. Run
multiple searches with different keyword combinations to maximize coverage:

1. Search with the most specific terms from the description
2. Search with broader platform/component terms
3. If a specific component or vendor is named, include it in a search

### Step 3: Read Top Matches

Read the full content of the top 3-5 matching articles/docs. Extract:
- Root cause patterns
- Resolution steps
- Known workarounds
- Escalation paths

### Step 4: Generate Structured Post

Output a structured triage post:

```
## Triage Summary

**Incident:** [number or description]
**Matched Sources:** [list with ids/paths]

### Probable Root Cause
[Based on evidence]

### Resolution Steps
1. [Step from <source>]
2. [Step from <source>]

### Escalation
[If sources indicate escalation needed]

### Sources
- <id/path>: [title] (grounded)
- Synthesized: [any steps not directly from a source, clearly labeled]
```

### Step 5: Self-Validate

Before presenting the output:
- Verify every cited source was actually returned by a search/read in this session
- Verify resolution steps match the source content (not paraphrased beyond recognition)
- Clearly label any synthesized content that is NOT from a cited source
