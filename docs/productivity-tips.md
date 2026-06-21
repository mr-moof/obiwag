# Productivity Tips for Claude Code

> **Source:** Adapted from Boris's Claude Code tips and Obi Wag workflow experience.
> **Last Updated:** 2026-02-02

---

## 1. Parallel Execution

### Multiple Worktrees
Run multiple git worktrees simultaneously, each with its own Claude session:

```bash
# Create a worktree for a feature branch
git worktree add ../my-feature feature/my-feature

# Work in the worktree with a separate Claude session
cd ../my-feature && claude
```

**When to use:**
- Large refactors affecting multiple areas
- Comparing approaches side-by-side
- Working on unrelated features simultaneously

### Shell Aliases
Create aliases for quick switching:

```bash
# ~/.bashrc or ~/.zshrc
alias cw1='cd ~/worktree-1 && claude'
alias cw2='cd ~/worktree-2 && claude'
```

```powershell
# PowerShell profile
function cw1 { Set-Location ~/worktree-1; claude }
function cw2 { Set-Location ~/worktree-2; claude }
```

---

## 2. Plan Mode Strategy

**"Pour your energy into the plan so Claude can one-shot the implementation."**

### When to Enter Plan Mode

Use `EnterPlanMode` or `/discovery` for:
- Tasks touching 3+ files
- New feature implementation
- Complex refactoring
- Unclear requirements

### Plan Mode Workflow

1. **Explore thoroughly** - Read relevant files, understand patterns
2. **Document findings** - Write discovery report
3. **Identify risks** - Note potential issues upfront
4. **Get approval** - Use `ExitPlanMode` when plan is solid

### Shift Back to Planning

If implementation hits issues:
- Stop coding
- Return to discovery/planning
- Understand the problem before trying again

---

## 3. Iterative CLAUDE.md Investment

**After every correction, consider updating documentation.**

### The Learning Loop

When Claude makes a mistake:
1. Correct the immediate issue
2. Ask: "Should this be documented to prevent recurrence?"
3. If yes, update relevant docs (CLAUDE.md, gotchas, policies)
4. The `/learning` phase automates this

### What to Document

- **Gotchas** - Non-obvious behaviors that caused issues
- **Patterns** - Successful approaches worth repeating
- **Anti-patterns** - Things to avoid
- **Vendor quirks** - API behaviors that differ from docs

---

## 4. Creating Custom Skills

Skills are reusable commands that encapsulate common workflows.

### Skill Structure

```markdown
# Skill Name

You are now acting as **[role-name]** - [brief description].

## Purpose
[What this skill does]

## Process
1. [Step 1]
2. [Step 2]
...

## Output Format
[Expected output structure]

## Completion Signal
- **Success:** `SKILL_NAME COMPLETE`
- **Failure:** `NEEDS INPUT: [reason]`
```

### Skill Location

- **Global skills:** `~/.claude/commands/`
- **Project skills:** `[project]/.claude/commands/`

### Examples of Useful Skills

| Skill | Purpose |
|-------|---------|
| `/discovery` | Research before implementing |
| `/review` | Code review with checklist |
| `/simplify` | Cleanup without behavior change |
| `/release` | Pre-deployment verification |

---

## 5. Effective Prompting

### Specification Detail

**Weak prompt:**
> "Add error handling"

**Strong prompt:**
> "Add error handling for the API call:
> - Retry 3 times with exponential backoff
> - Log each retry with attempt number
> - Return structured error on final failure"

### Request Rigorous Review

Before implementation, ask:
> "Review this plan for potential issues before we implement"

### Iterate on Solutions

If first version isn't right:
> "This approach has [problem]. Propose an alternative that addresses [specific concern]"

---

## 6. Subagent Delegation

### When to Use Subagents

Use `Task` tool with subagents for:
- **Exploration** - `subagent_type=Explore` for codebase research
- **Planning** - `subagent_type=Plan` for architecture decisions
- **Parallel work** - Multiple independent tasks

### Benefits

- Keeps main context clean
- Distributes computational load
- Enables parallel execution

### Best Practices

1. **Clear prompts** - Agent starts fresh, provide full context
2. **Specific scope** - Define exactly what the agent should find/do
3. **Check results** - Verify agent findings before acting on them
4. **Use built-in tools** - Prefer Glob/Grep/Read over `find`/`grep`/`cat` via Bash (see gotchas #11)

### Example

```
Task: Find all files that handle authentication
subagent_type: Explore
prompt: "Search for authentication handling code. Look for:
- Login/logout functions
- Token validation
- Session management
Return file paths and brief description of each."
```

---

## 7. Environment Optimization

### Status Line

Use `/statusline` to see context information:
- Current file
- Git status
- Token usage

### Terminal Setup

**Recommended:**
- Modern terminal (Ghostty, iTerm2, Windows Terminal)
- Color-coded tabs for different worktrees
- Voice dictation for faster input

### Session Management

- Use `/compact` to summarize and reduce context
- Start new sessions for unrelated tasks
- Keep related work in single session for continuity

---

## Integration with Obi Wag

These tips complement the Obi Wag workflow:

| Boris Tip | Obi Wag Implementation |
|-----------|----------------------|
| Plan mode | `/discovery` phase |
| CLAUDE.md investment | `/learning` phase + memory system |
| Custom skills | All workflow commands |
| Bug fixing | `/author` + `/review` cycle |
| Subagents | Task agents in each phase |

---

*Tips sourced from Boris via ykdojo.github.io/claude-code-tips*
