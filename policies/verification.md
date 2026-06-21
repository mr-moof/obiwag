# Verification Policy

> Every claim of success must include evidence.

## Rules

1. **Show evidence after every fix** - File contents, test output, or command results. The words "fixed" or "done" must not appear without accompanying proof.
2. **Broaden on failure** - When a fix attempt fails, do not retry the same hypothesis. Broaden diagnostic scope: check adjacent systems, read error logs, verify assumptions.
3. **No premature success claims** - Do not claim completion until verification passes. If tests fail or evidence is ambiguous, report the current state honestly.

## What counts as evidence

- Test output showing pass/fail
- File contents confirming the change is in place
- Command output demonstrating correct behavior
- Git diff showing the intended change

## What does NOT count

- "I made the change" without showing the result
- Repeating what you intended to do
- Describing what should happen without confirming it did
