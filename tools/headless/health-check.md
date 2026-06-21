You are running in headless mode. Do not ask questions. Complete the task and output structured results.

## Task: Obi Deployment Health Check

Verify that the Obi Wag deployment is functional.

### Checks

1. **Python reachable**: Run `C:\Python314\python.exe --version` and confirm it returns Python 3.14.x
2. **Hook files exist**: Read ~/.claude/settings.json, extract all hook command paths, verify each file exists
3. **settings.json valid**: Confirm ~/.claude/settings.json parses as valid JSON
4. **No heredoc pollution**: Read ~/.claude/settings.local.json (if it exists), check for any entries longer than 200 characters (indicates heredoc pollution)
5. **Calibration parseable**: Read ~/.claude/.obi/calibration.md, confirm it has valid YAML frontmatter between --- markers
6. **Required commands present**: Verify all 17 required commands exist in ~/.claude/commands/
7. **Required agents present**: Verify all 5 required agents exist in ~/.claude/agents/

### Output Format

Output a JSON summary:
```json
{
  "status": "healthy|degraded|broken",
  "checks": {
    "python": "pass|fail",
    "hook_files": "pass|fail",
    "settings_json": "pass|fail",
    "no_heredoc_pollution": "pass|fail",
    "calibration": "pass|fail",
    "commands": "pass|fail",
    "agents": "pass|fail"
  },
  "failures": []
}
```
