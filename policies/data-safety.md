# Data Safety Policy

> Every authoritative artifact has a backup.  Derivable artifacts do not.

Motivation: the quiet-shift reset of 2026-03 destroyed ~75 files under
`~/.claude/projects/.../memory/` with no path to recover.  This policy
defines what is backed up, how, and how to restore.

## Authoritative vs derivable

| Artifact | Location | Authoritative? | Backup mechanism |
|---|---|---|---|
| Project auto-memory | `~/.claude/projects/<encoded>/memory/*.md` | **Yes** — user-curated corrections, feedback, references | `project_memory.backup_project_memory` on Stop hook. See `hooks/core/project_memory.py`. |
| Calibration tuning | `~/.claude/.obi/calibration.md` | **Yes** — hand-edited thresholds and safety flags | `obi_state_backup.backup_obi_state` on Stop hook.  See `hooks/core/obi_state_backup.py`. |
| Pending learnings | `~/.claude/.obi/pending-learnings.json` | **Yes** — queue of learnings not yet reviewed | `obi_state_backup.backup_obi_state` on Stop hook. |
| Obi session summaries | `~/.claude/.obi/memory/sessions/*.md` | Observational only — reproducible from git commit history | None. |
| Deployment manifest | `~/.claude/.obi/deployment-manifest.json` | Derivable — regenerated on next `tools/deploy.ps1` | None. |
| Hook logs | `~/.claude/.obi/hook-errors.jsonl`, `hook-execution-log.jsonl` | Observational — append-only diagnostic logs | None. |
| Project cache | `~/.claude/.obi/project-cache.json` | Derivable — rebuilt on next session-start mtime miss | None. |
| Session quality | `~/.claude/.obi/session-quality.jsonl` | Observational | None. |
| Claude Code settings | `~/.claude/settings.json`, `settings.local.json` | Authoritative but managed separately | `config-guardian.ps1` with rotation in `~/.claude/.obi/config-snapshots/`. |

## Backup cadence and location

- **Trigger**: the Stop hook runs backups at the end of every session,
  inside a 500 ms soft time budget.  Failures are silent — backups never
  block session shutdown.
- **Change detection**: each backup module fingerprints the source
  (`size` + integer-second `mtime`) and compares to the most recent
  snapshot's `backup-index.json`.  Identical content means no new
  snapshot is written.
- **Rotation**:
  - `project-memory/`: last 3 snapshots (see `project_memory.MAX_SNAPSHOTS`).
  - `obi-state/`: last 5 snapshots (see `obi_state_backup.MAX_SNAPSHOTS`).
- **Location**: `~/.claude/.obi/backups/<subsystem>/snapshot-YYYYMMDD-HHMMSS/`.

## Detection

`SessionStart` runs `drift_detector.detect_drift()` which compares the
deployed Obi state against the source repo and reports any drifted
files in the welcome message.  This is *configuration* drift, not
backup staleness.  Backup staleness is not surfaced today — if you
need to verify a recent snapshot exists, list the backup directories
directly:

```powershell
ls "$HOME\.claude\.obi\backups\project-memory"
ls "$HOME\.claude\.obi\backups\obi-state"
```

## Recovery

If any authoritative file is missing or corrupt:

1. **Locate the latest good snapshot** by inspecting
   `backup-index.json` in each `snapshot-*/` directory.  The `timestamp`
   field identifies the snapshot; `fingerprints` shows what was captured.
2. **Copy files back manually** — do not run `tools/deploy.ps1` blindly,
   as deploy overwrites source-tracked files but does *not* restore the
   contents of `.obi/`.
3. **For `memory/` recovery**: copy files from
   `~/.claude/.obi/backups/project-memory/snapshot-<timestamp>/` into
   `~/.claude/projects/<encoded>/memory/`.  Do not copy
   `backup-index.json` itself.
4. **For `calibration.md` / `pending-learnings.json`**: copy from
   `~/.claude/.obi/backups/obi-state/snapshot-<timestamp>/` back into
   `~/.claude/.obi/`.

After recovery, run `.\tools\config-guardian.ps1 -CheckOnly` to confirm
the hook environment is still healthy.

## What this policy does NOT cover

- **Source repo loss** — the obiwag-agents git repo is backed by
  `github.com`.  Clone it fresh if the local tree is gone.
- **Claude Code internal state** — `~/.claude/projects/<encoded>/state/`
  is Claude Code's territory, not Obi's.  Nothing in this repo should
  back it up.
- **User filesystem outside `~/.claude/`** — working directories, git
  repos, and OS state are out of scope.
