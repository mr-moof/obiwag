---
name: sql-safety
description: SQL query safety rules for SQL-backed MCP servers and data sources. Auto-apply when writing or executing SQL queries.
user-invocable: false
---

# SQL Safety Rules

## Read-Only Enforcement

**SELECT only.** Never generate INSERT, UPDATE, DELETE, DROP, TRUNCATE, ALTER, or EXEC statements. Read-only data sources should never receive write attempts.

## Query Discipline

- **No `SELECT *`** — always specify columns explicitly
- **Always use `TOP 100`** (or an appropriate `LIMIT`) unless the user requests a full result set
- **Qualify table names** with schema: `dbo.orders`, not just `orders`

## Available Data Sources

This framework ships with no data sources preconfigured. Document the SQL sources
you connect (MCP server name, server, database, auth) in `docs/data-sources.md`.
Example shape:

| MCP Server | SQL Server | Database | Auth |
|------------|-----------|----------|------|
| `analytics` | `analytics-sql` | `AnalyticsDB` | Integrated / Windows Auth |

## Join Discipline (example)

```sql
-- Specify columns, qualify schema, limit rows
SELECT TOP 100 o.OrderId, o.Status, c.CustomerName
FROM dbo.Orders o
JOIN dbo.Customers c ON c.CustomerId = o.CustomerId
WHERE o.Status = 'Active'
```

**Generic gotchas worth verifying per source:**
- A *view* may be invisible to `INFORMATION_SCHEMA.TABLES` — confirm via the
  source's schema-discovery tools, not assumptions.
- A table may lack the audit column you expect (e.g. no `created_on`) — filter on
  a column you have confirmed exists.
- Join keys are frequently NOT same-named across tables — verify the real
  relationship before joining.

## SQL in Bash Commands

**Never inline SQL in Bash commands** — the pre_tool_use hook can false-match SQL keywords. Instead:

1. Write SQL to a temp file: `Write` tool → `/tmp/query.sql`
2. Reference it if needed
3. Or use the MCP `execute_sql` tool directly (preferred)

## Schema Discovery

Use MCP tools to explore schema before writing queries:
- `list_tables` — see available tables
- `describe_table` — column names, types, nullable
- `search_columns` — find columns across tables

See `docs/data-sources.md` for the catalog of sources you have documented.
