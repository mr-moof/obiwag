# Obi Wag FAQ

> **Version:** 1.0 | **Last Updated:** 2026-01-29

Frequently asked questions about Obi Wag.

---

## General Questions

### What happens if I interrupt `/obi-auto`?

The workflow **pauses safely**. State is preserved in `.obi/ralph-state.json`:

1. Your work is not lost
2. Any completed phases remain completed
3. You can resume from where you stopped

**To resume:** Run `/obi-auto` again in the same project directory. Obi will detect existing state and offer to resume or start fresh.

**To start over:** Delete `.obi/ralph-state.json` and run `/obi-auto` again.

### Is the memory system required?

**No.** The memory system is optional. Claude Code functions normally without it.

The memory system provides:
- Pattern injection (grounding sources at session start)
- Correction tracking (learns from your feedback)
- Calibration evolution (proposes behavior improvements)

If hooks fail or aren't configured, Obi still works—you just lose the adaptive learning features.

### What if my network drops during deployment?

**Deployment is idempotent.** You can safely re-run `.\tools\deploy.ps1` or the manual copy commands:

1. Files are copied atomically (complete or not at all)
2. Existing files are overwritten with fresh versions
3. No partial state to clean up

**To verify after reconnection:**
```powershell
python tools\healthcheck.py --quick
```

---

## Workflow Questions

### When should I use `/obi` vs `/obi-auto`?

| Use `/obi` (manual) when... | Use `/obi-auto` (autonomous) when... |
|----------------------------|--------------------------------------|
| Learning the workflow | Confident in your requirements |
| Exploring options | Task is well-defined |
| Complex debugging | Standard feature/fix |
| Want step-by-step control | Want hands-off execution |

**Tip:** Start with `/obi` to learn, graduate to `/obi-auto` for routine work.

### Why does Obi stop and ask for input?

Obi stops when it encounters a **break signal**:

| Signal | Meaning | What to do |
|--------|---------|------------|
| `NEEDS USER INPUT` | Missing information | Provide the requested details |
| `HARD STOP` | Policy violation | Review the violation, provide evidence or change approach |
| `3-STRIKE LIMIT` | Repeated failure | Provide new information or try different approach |

These stops **prevent wasted effort**. Better to pause and clarify than iterate blindly.

### What counts toward the 25-line Express Lane threshold?

**Included:**
- Code files: `.ps1`, `.psm1`, `.psd1`, `.cs`, `.go`, `.py`, `.ts`, `.js`
- Net additions + deletions (not modified lines)

**Excluded:**
- Test files (`*.Tests.ps1`, `*_test.go`, etc.)
- Documentation (`.md`)
- Configuration (`.json`, `.yaml`, `.xml`)
- Generated code
- Comments-only changes

**Example:** 18 lines in `Feature.ps1` + 40 lines in `Feature.Tests.ps1` = **18 lines** (Express Lane applies)

---

## Troubleshooting

### Commands don't work after deployment

1. **Exit and restart Claude Code:**
   ```powershell
   exit
   claude
   ```

2. **Verify files exist:**
   ```powershell
   ls ~/.claude/commands/
   ```

3. **Check for typos:** Commands are case-sensitive (`/obi` not `/Obi`)

### Hooks are blocking everything

**Emergency fix:**
```powershell
# Disable hooks
claude config set hooks.enabled false

# Or rename settings.json to disable hooks defined there
Rename-Item "$env:USERPROFILE\.claude\settings.json" "$env:USERPROFILE\.claude\settings.json.disabled"
```

Fix the issue, then re-enable:
```powershell
claude config set hooks.enabled true
```

Alternatively, edit `~/.claude/settings.json` and temporarily rename the `"hooks"` key to `"hooks_disabled"` to prevent hook execution without removing the configuration.

### "MISSING SOURCE" error keeps appearing

This is the zero-hallucination policy working correctly. You need to provide evidence:

1. **Check existing wrappers/references:** Look in `docs/domain-patterns/` or `docs/` for the API
2. **Add documentation:** If it's a new API, document it first
3. **Provide reference:** Show Obi a working example from the codebase

**Never** ask Obi to bypass this check—it protects you from hallucinated APIs.

### Health check shows "DRIFT DETECTED"

Deployed files differ from source files. This happens when:
- Manual edits were made to deployed files
- Source was updated but deployment didn't run

**Fix:**
```powershell
.\tools\deploy.ps1  # redeploy from source
python tools/healthcheck.py --quick
```

---

## Platform-Specific

### Windows: Script won't run (execution policy)

If `tools/deploy.ps1` is blocked by PowerShell's execution policy, allow local/signed
scripts for the current session and re-run the deploy:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\tools\deploy.ps1
```

If `pwsh` (PowerShell 7+) is installed, you can also run the script with it directly:

```bash
pwsh -File tools/deploy.ps1
```

### Hooks or settings disappear between sessions

If `~/.claude/` lives on a non-persistent or roaming profile, deployed hooks and Claude
Code auth tokens may not survive a logout. Re-run the deploy after logging back in, then
confirm Claude Code is still authenticated:

```powershell
claude --version
```

---

## Questions Not Answered Here?

Open an issue in the project repository.
