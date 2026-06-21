---
description: Check for and apply updates to Obi Wag from GitHub.
allowed-tools: Read, Glob, Grep, Bash
---

# Obi Update Command

Check for and apply updates to Obi Wag.

## Process

### 1. Show Current Version

Display the current installed version:
```python
from core.version import get_version_info
info = get_version_info()
print(f"Current version: {info}")
```

Report:
- Version number
- Commit hash (if available)
- Whether local changes exist

### 2. Check for Updates

Fetch from remote and check for available updates:
```python
from core.version import check_for_updates
has_updates, message, commits = check_for_updates()
```

If updates available:
- Show number of available commits
- List commit summaries

If already up to date:
- Report "Already up to date"

### 3. Apply Updates (With the user Approval)

**IMPORTANT:** This step requires explicit the user approval before proceeding.

If the user approves:
```python
from core.version import apply_updates
success, message = apply_updates()
```

Report:
- Success or failure
- What was updated

### 4. Verify Update

After successful update:
- Run the hook tests to verify nothing broke:
  ```bash
  cd ~/.claude/hooks && python -m pytest tests/ -v
  ```
- Start a new session to verify hooks load correctly

## Output Format

```
## Obi Update Status

### Current Version
Version: v0.31 (abc1234)
Local changes: None

### Update Check
Status: [N update(s) available | Already up to date]

[If updates available:]
### Available Updates
- abc1234 feat: Add new feature
- def5678 fix: Fix bug

### Action Required
the user approval required to apply updates.
[Await approval before running apply_updates()]

### Update Result
[After applying:]
Status: [Success | Failed]
Message: [details]

### Verification
- Hook tests: [PASS | FAIL]
- Ready for new session
```

## Error Handling

- If not a git repository: Report that updates are not available
- If fetch fails: Report network or permission issue
- If pull fails: Report conflicts or other git issues
- Never leave the system in a broken state
