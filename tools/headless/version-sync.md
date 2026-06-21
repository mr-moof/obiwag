You are running in headless mode. Do not ask questions. Complete the task and output structured results.

## Task: Version Sync Check

Verify all versioned files in the obiwag-agents repo report the same version number.

### Steps

1. Read `version.yaml` from the repo root to get the canonical version
2. Check each versioned file for consistency:
   - `CLAUDE.md` (Version line in header)
   - `version.yaml`
   - `README.md` (version badge or header)
   - `tools/deploy.ps1` (if version is referenced)
   - `tools/bump-version.ps1` (if version is referenced)
   - Any other files containing a version string matching the pattern `\d+\.\d+`
3. Report mismatches

### Output Format

Output a JSON summary:
```json
{
  "status": "synced|mismatch",
  "canonical_version": "0.60",
  "files_checked": [],
  "mismatches": []
}
```
