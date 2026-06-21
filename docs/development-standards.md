# Development Standards

Standards for Obi Wag development.
Moved here from CLAUDE.md to keep the main file lean — only universally essential instructions live there.

---

## Git Workflow

**Obi Wag repos (obiwag-agents, single maintainer):** Commit direct to master. No feature branch / PR ceremony required.

**Branch prefixes (when a branch IS used, e.g. for parallel work or risky changes):** `feature/`, `fix/`, `docs/`, `refactor/`

**Commit format:** Conventional commits (`feat:`, `fix:`, `docs:`, `refactor:`)

---

## Testing

```bash
cd ~/.claude/hooks && python -m pytest tests/       # Run all hook tests
python -m pytest tests/test_memory_reader.py -v     # Run specific test
```

Before committing: Run hook tests, verify CLAUDE.md references resolve, test affected commands.

---

## Markdown Conventions

- One sentence per line (diff-friendly)
- Use `**bold**` for emphasis, not ALL CAPS

---

## Repo Root

The repo root may contain only README.md, CLAUDE.md, AGENTS.md, DEPLOYMENT.md, LICENSE, CHANGELOG.md, and `.gitignore`-class dotfiles; point-in-time snapshots belong in `docs/archive/`.

---

## Data Sources

See `docs/data-sources.md` for the catalog of queryable data sources (SQL servers, MCP servers, CLI tools).
Check it during discovery before proposing new dependencies.

---

## Graphify Artifacts

[Graphify](https://github.com/google/graphify) generates a code+docs graph used to spot god-nodes, cross-community bridges, and high-degree rule hotspots. Re-run when:

- Hook system gains new entry points or splits an existing module.
- A new top-level docs hub is added (frontmatter on existing hubs is what graphify keys on for naming).
- You want a refreshed view before a substantial refactor.

### How to run

```powershell
graphify update .
```

Outputs land in `graphify-out/`. Inspect:

- `GRAPH_REPORT.md` — human-readable summary; commit it.
- `graph.html` — interactive browser visualization (4-5 MB; not committed).
- `graph.json` — full graph data for tooling (6-7 MB; not committed).

### What to commit

| Artifact | Commit? | Why |
|----------|---------|-----|
| `GRAPH_REPORT.md` | yes | Small, diff-readable, useful in reviews. |
| `graph.json` | no | 6+ MB; regenerated each run; bloats history. |
| `graph.html` | no | 4+ MB; binary-ish; same regeneration story. |
| `cache/` | no | Per-machine working set. |
| `.graphify_*.json` | no | Pipeline intermediates. |
| `manifest.json`, `cost.json` | no | Run metadata, not graph data. |

`.gitignore` enforces all "no" rows. To commit `graph.json` or `graph.html` for a one-off snapshot (e.g. preserving a pre-refactor baseline), use `git add -f <path>` and note the snapshot reason in the commit message.

### Caveats

- **API key absence.** When `GRAPHIFY_API_KEY` is not set, the semantic document layer is built with a local heuristic. The graph still surfaces structural patterns reliably, but anything labeled "semantic" should be treated as approximate.
- **No automated re-run.** Graphify is not part of CI. Refresh by hand when the conditions above are met.

---

## Artifact Locations

Autonomous mode writes to `.obi/`:
- `.obi/discovery-report.md` - Discovery findings
- `.obi/reviews/` - Review outputs
- `.obi/state/` - Phase state files
