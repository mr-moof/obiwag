# Operation Timeouts

Before any operation expected to take >10 seconds: **announce** what you're about to do, **estimate** duration, then **execute**.

## Timeout Table

| Operation Type | Timeout |
|----------------|---------|
| ExitPlanMode | 2 minutes |
| Task agent | 3 minutes |
| Skill invocations | 5 minutes |
| Test runs | 3 minutes |
| Build operations | 10 minutes |

If the timeout is reached: assume failure, report to the user, offer options.

## When the user reports a stall

> If the user says the session is stuck, hung, or stalled, he is right by definition — from the
> operator's seat, silence is stuck regardless of internal classification. NEVER respond with
> terminology corrections ("that was a dropped result, not a hang"). Respond with exactly three
> things, then act: (1) what was dispatched, (2) what state you verified, (3) the next action being
> taken now. The sentinel taxonomy below exists to drive YOUR recovery behavior, not to adjudicate
> the user's word choice.

## Tool-result drops — sentinel-recovery contract

A `[Tool result missing due to internal error]`, empty, or whitespace-only tool
result still leaves you in control, so react immediately — treat it internally as a
returned result requiring immediate reaction (do not idle); this is a recovery rule, not
language to use with the user. Never idle, never re-issue blindly, never stop to ask first.

**Observability (emit, don't recover silently).** BEFORE the verify read in step 1, emit one line
to the terminal: `DROP DETECTED: <tool/phase> — verifying with <check>, then <retry once | proceed
| halt>`. After the check resolves, emit one outcome line: `DROP RESOLVED: <already-applied |
retried-ok | escalating>`. From the terminal, silent verification is indistinguishable from a
freeze, so these lines are the visible evidence recovery is running. If you reach the step-1 read
after a sentinel without having emitted the DETECTED line, you skipped the contract — emit it now.
Stable contract: grep-stable on `^DROP (DETECTED|RESOLVED):`.

1. **Verify, don't assume.** A drop does NOT mean the action failed — some land,
   some don't. Confirm actual state with one cheap read matched to the action:
   `Grep`/`Read` for an edit, `git status`/`git log -1` for a commit,
   `SELECT count(*)` for a DB write, `Test-Path` for a file.
2. **Branch on what you found.** Did not happen → retry **once**, now bounded
   (explicit `timeout`) or `run_in_background` per the duration guard. Already
   happened → proceed; do not retry (re-running an edit/commit can double-apply).
3. **Keep moving.** Escalate to the user only if state is genuinely unknowable after
   the check — and even then work independent steps meanwhile rather than idling.

The **true silent-hang** (no sentinel ever returns) cannot be detected from
inside a blocking call, so it is **prevention-only**: the PreToolUse duration
guard (`hooks/pre_tool_use.py::check_duration_guard`) forces likely-long
commands to background or carry a bounded timeout, so a drop costs seconds, not
an idle hour. Backgrounded work is polled via its output file.
