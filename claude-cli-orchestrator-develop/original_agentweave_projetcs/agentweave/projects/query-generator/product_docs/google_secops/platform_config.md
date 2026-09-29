# Platform Configuration — Google SecOps

> **Purpose:** Authoritative platform-specific configuration for Google SecOps as a destination
> platform. Agents load this file to resolve platform-dependent behavior (query language name,
> MCP tool names, illegal constructs) without hardcoding these values in agent instructions.

---

## Platform Identity

| Key | Value |
|---|---|
| `platform_id` | `google_secops` |
| `query_language` | `YARA-L 2.0` |
| `query_language_short` | `YARA-L` |

---

## Migration Type → Query Structure File Mapping

| `migration_type` | Query Structure Reference File |
|---|---|
| `rule` | `product_docs/google_secops/query_structure/secops_rule_syntax.md` |
| `query` | `product_docs/google_secops/query_structure/secops_query_syntax.md` |

Use these file paths when agents need to load the detailed syntax reference for V4 structure
validation or query construction.

---

## MCP Validation Server

| Key | Value |
|---|---|
| `mcp_server_name` | `google-secops-mcp-server` |
| `mcp_prefix` | `mcp__google-secops-mcp-server` |

---

## Migration Type → MCP Validation Tool Mapping

| `migration_type` | Full MCP Tool Name |
|---|---|
| `rule` | `mcp__google-secops-mcp-server__validate_rule` |
| `query` | `mcp__google-secops-mcp-server__validate_dashboard_query` |

**How to resolve at runtime:**
1. Read `migration_type` from agent input
2. Look up the full MCP tool name from the table above
3. Use that exact string as the tool name when calling the MCP validation tool

---

## V4 Illegal Construct Patterns

The following constructs from **source platforms** are illegal in YARA-L and must NOT appear
in any generated query. Flag their presence as a V4 structural failure.

| Illegal Pattern | Description | Check Method |
|---|---|---|
| `\|` (pipe character) | SPL pipe operator — YARA-L has no pipe syntax | Substring presence |
| `^SELECT\s` (regex) | SQL SELECT keyword as standalone statement opener | Regex match |
| `^FROM\s` (regex) | SQL FROM keyword as standalone clause | Regex match |
| `^WHERE\s` (regex) | SQL WHERE keyword as standalone clause | Regex match |

> **Note:** The `\|` pipe check applies when `source_platform = "splunk"` or any other
> pipe-based platform. The SQL keywords apply when `source_platform` uses SQL-like syntax.
> When in doubt, flag any pattern that is clearly incompatible with YARA-L syntax.

---

## Required Sections by Migration Type

### For `migration_type = "rule"`:
| Section | Required | Notes |
|---|---|---|
| `meta:` | Yes | Must contain `description` and `severity` at minimum |
| `events:` | Yes | Must define at least one event variable |
| `match:` | Conditional | Required when correlation or aggregation is used |
| `outcome:` | Conditional | Required when calculating aggregates in condition |
| `condition:` | Yes | Must define the trigger logic |

### For `migration_type = "query"` (dashboard/search):
| Section | Required | Notes |
|---|---|---|
| `events:` | Yes | Must define filter conditions |
| `match:` | Conditional | Required when grouping is used; use `by` (tumbling window) |
| `outcome:` | Conditional | Required when aggregation metrics are calculated |
| `order:` | Optional | Only valid when `match` is present |
| `limit:` | Optional | Only valid when `match` is present |

---

## Logtype Filter Format

When `destination_metadata.log_type` is non-empty, the logtype filter must be injected as
the **FIRST** condition in the `events:` section.

**Format for rules (with event variable):**
```yara-l
$e.metadata.log_type = "LOGTYPE_VALUE"
```

**Format for dashboard queries (without event variable):**
```yara-l
metadata.log_type = "LOGTYPE_VALUE"
```
