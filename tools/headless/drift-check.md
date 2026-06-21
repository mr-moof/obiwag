You are running in headless mode. Do not ask questions. Complete the task and output structured results.

## Task: Deployment Drift Detection

Compare deployed files in ~/.claude/ against the source repo at the path stored in CLAUDE_PROJECT_ROOT (or default C:/src/obiwag-agents/).

### Steps

1. Read the deployment manifest at ~/.claude/.obi/deployment-manifest.json
2. For each mapping in the manifest, compare the deployed file against the source file:
   - Read both files
   - Report if content differs (drift detected)
   - Report the drift direction: "deployed newer" or "source newer" based on which has more content or recent changes
3. Check for files in ~/.claude/commands/ that are NOT in the manifest (orphaned files)
4. Check for source files in orchestration/utilities/ that are NOT in the manifest (undeployed files)

### Output Format

Output a JSON summary:
```json
{
  "status": "clean|drift_detected",
  "checked": 0,
  "drifted": [],
  "orphaned": [],
  "undeployed": [],
  "details": []
}
```

If drift is detected, create a GitHub issue with the drift details:
```bash
gh -R user/obiwag-agents issue create --title "Deployment drift detected" --body "..." --label "maintenance"
```
