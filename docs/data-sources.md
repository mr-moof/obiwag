# Data Source Catalog

> **Ceiling:** Keep this file under 300 lines. If it exceeds that, split into per-source files in `docs/data-sources/`.

Known data sources available to this project. Check here **before** proposing a
new dependency — the answer may already exist.

This catalog ships **empty** on purpose: the framework is domain-agnostic.
Document each data source you connect so future sessions discover it instead of
reinventing connection boilerplate.

**Boundary with `docs/domain-patterns/`:**
This file is a **catalog** — what data sources exist, how to connect, what
questions they answer. `docs/domain-patterns/*.md` documents **how to use vendor
APIs** — auth patterns, error handling, code examples. When both apply, link from
here to the domain-patterns file for API details.

---

## SQL Servers

Document each SQL source: connection, auth, availability, and the key
tables/views with the questions they answer.

### Example: AnalyticsDB

| Field | Value |
|-------|-------|
| **Connection** | `Server=analytics-sql;Database=AnalyticsDB;Integrated Security=True` |
| **Auth** | Integrated / Windows Auth (no credentials in code) |
| **Availability** | _where it's reachable from_ |
| **Used by** | _which projects/commands_ |

**Key tables/views:**

| Table | Answers | Notes |
|-------|---------|-------|
| `dbo.Orders` | Order status, totals | Join to `dbo.Customers` via `CustomerId`. |

See `skills/sql-safety/SKILL.md` for query-safety rules that apply to every SQL source.

---

## MCP Servers

Document each MCP server: type, auth, availability, the tools it exposes, and any gotchas.

### Example: analytics (SQL MCP)

| Field | Value |
|-------|-------|
| **Type** | MCP server (local) |
| **Auth** | Passthrough to the SQL source |
| **Tools** | `list_tables`, `describe_table`, `search_columns`, `execute_sql` |

Provides interactive SQL access without writing connection boilerplate.

---

## CLI Tools

### gh (GitHub CLI)

| Field | Value |
|-------|-------|
| **Type** | CLI |
| **Auth** | `gh auth login` (token stored by `gh`) |
| **Used by** | issue / PR / pipeline workflows (`/fixissue`, `/obi-swarm`, pipeline monitor) |

Issues, PRs, runs, and releases for GitHub repositories.

#### REST endpoints used by `tools/probes/` (rigor=max Phase 0)

rigor=max Phase-0 probes call the GitHub REST API via `gh api`:

| Endpoint | Probe | Purpose |
|----------|-------|---------|
| `GET /users/<owner>` (falls back to `GET /orgs/<owner>`) | `tools/probes/namespace_kind.ps1` | Distinguishes a user account (`type: "User"`) from an organization (`type: "Organization"`). |
| `GET /repos/<owner>/<repo>/actions/runners` | `tools/probes/runner_tags.ps1` | Returns the self-hosted runner list with `labels` per runner (GitHub-hosted runners need no check). |
| `GET /repos/<owner>/<repo>/pages` | `tools/probes/pages_access.ps1` | Returns Pages config: `html_url`, `status`, `source`, `public`. |
| `GET /repos/<owner>/<repo>` | `tools/probes/mirror_existence.ps1` | Returns the repo record; presence confirms the target repo exists. |

Marketplace reach (`tools/probes/marketplace_reach.ps1`) is a plain anonymous HTTP probe — not a `gh api` call.
