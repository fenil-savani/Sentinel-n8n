---
name: Generate Sentinel Analytic Rule
description: Generate a Microsoft Sentinel scheduled analytic (detection) rule — YAML with a MITRE-mapped KQL query, trigger logic, and entity mappings — from a plain-language detection scenario.
---

# Generate a Microsoft Sentinel Analytic Rule

You are generating a **Microsoft Sentinel scheduled analytic rule** (a detection rule YAML) that
turns a plain-language detection scenario into a KQL query with trigger logic, MITRE ATT&CK mapping,
and entity mappings for the investigation graph. The output must be production-ready and follow the
rules below verbatim.

KQL reference if the query requires more complex functions: <https://learn.microsoft.com/en-us/kusto/?view=microsoft-fabric>
MITRE ATT&CK reference for tactics/techniques: <https://attack.mitre.org>

---

## Step 1 — Collect input from the user

Ask the user for, whichever they haven't already given:

1. **The detection scenario in plain language** — what behavior/threat, what data source, what
   condition should trigger it (required).
2. **The table or parser to query** — the normalized parser function name if one exists, otherwise
   the raw `_CL` table (required).
3. **Severity** — Low / Medium / High / Informational.
4. **The connector this rule depends on** — its `connectorId` (from the Data Connector's definition)
   and the table(s)/`dataTypes` it writes.
5. **Trigger logic** — how often the rule runs (`queryFrequency`) and over what lookback window
   (`queryPeriod`), plus the threshold condition (operator + value). If not given, propose a sensible
   default (e.g. hourly, `gt 0`) and say so in your `notes` to the analyst.
6. **Whether it should auto-create an incident** (default yes for a real detection).
7. **Entities to map** for the investigation graph (IP, Account, Host, URL, FileHash, etc.) and which
   query columns hold them.
8. **Watchlists** for exclusions (e.g. known-good IP list), instead of hardcoding values, if relevant.

If the user hasn't researched **tactics and techniques**, derive them yourself from the scenario using
real MITRE ATT&CK values — never invent a tactic/technique that doesn't exist, and never leave them
empty; if genuinely unsure, pick the closest defensible tactic and say so in `notes`.

## Step 2 — Validate scenario completeness

Before writing anything, verify you actually know:

- [ ] What table/parser the query reads from, and that its fields are known to you (ask, or read a
  reference parser if one is pointed at)
- [ ] What the trigger condition is (not just "detect X" — the actual KQL condition that fires it)
- [ ] Severity
- [ ] At least one entity mapping

If anything is missing, **stop and ask** rather than guessing. Never hardcode a specific IP, domain,
subscription id, or tenant id into the query — use `isnotempty()`/watchlists instead.

## Step 3 — Generate the rule following these rules

**The output must be a single YAML document.** Use this structure exactly:

```yaml
id: <new GUID>                      # generate once; never invent a second one
name: <Descriptive Detection Name>
description: |
  Detects <threat/behavior> in <data source>. Fires when <condition>, indicating <scenario>.
severity: Medium                    # Low | Medium | High | Informational
status: Available
requiredDataConnectors:
  - connectorId: <ConnectorId>
    dataTypes: [ <TableName>_CL ]
queryFrequency: 1h                  # e.g. 5m, 1h, 1d
queryPeriod: 1h                     # lookback window, same units
triggerOperator: gt                 # gt | lt | eq | ne
triggerThreshold: 0
tactics: [ InitialAccess ]          # real MITRE tactics only, only those that apply
techniques: [ T1078 ]               # real MITRE technique IDs only (Txxxx or Txxxx.xxx)
query: |
  <parser_or_table>
  | where TimeGenerated > ago(1h)
  | where <the actual detection condition>
  | project TimeGenerated, <fields the entity mappings reference>, <other useful columns>
entityMappings:
  - entityType: IP
    fieldMappings: [ { identifier: Address, columnName: SourceIp } ]
version: 1.0.0
kind: Scheduled
```

### Rule construction rules

- **`id`** — a fresh UUID, generated once. This never changes on a resubmission of the same rule concept.
- **`severity`** — one of `Informational`, `Low`, `Medium`, `High` exactly (case-sensitive).
- **`tactics`/`techniques`** — real MITRE ATT&CK values only. Valid tactics: `InitialAccess`, `Execution`,
  `Persistence`, `PrivilegeEscalation`, `DefenseEvasion`, `CredentialAccess`, `Discovery`,
  `LateralMovement`, `Collection`, `CommandAndControl`, `Exfiltration`, `Impact`. Techniques look like
  `T1078` or `T1059.006`. Never fabricate a technique id that doesn't fit this shape.
- **`requiredDataConnectors`** — `connectorId` must be the connector's real id (ask if unknown; do not
  invent one) and `dataTypes` must match the table name(s) the query actually reads, exactly
  (case-sensitive, including the `_CL` suffix for custom tables).
- **`queryFrequency`/`queryPeriod`** — short duration form (`5m`, `1h`, `1d`), not ISO-8601 — that
  conversion happens at deploy time, not here.
- **Query hygiene** — no hardcoded IPs, domains, URLs, subscription ids, or tenant ids; use
  `isnotempty()` or a watchlist lookup (`_GetWatchlist('<name>')`) instead. Always end with an explicit
  `| project` (never `project *`) that includes every column referenced by an entity mapping, plus
  `Type` if the source table doesn't already normalize it away.
- **`entityMappings`** — at least one, each `columnName` must be a column the final `project` actually
  outputs. Use the standard `entityType`/`identifier` pairs: `IP`/`Address`, `Account`/`Name` (or
  `FullName`), `Host`/`HostName`, `URL`/`Url`, `FileHash`/`Algorithm`+`Value`.
- **`kind`** — always `Scheduled` for what you generate here (NRT and Fusion rules are out of scope).
- **No hardcoded values** — never bake in a specific customer's IP, hostname, or credential.

## Reference patterns

- A rule with no natural entity beyond the source host still needs at least a `Host` mapping — don't
  submit a rule with an empty `entityMappings` list.
- If the analyst didn't say whether to auto-create an incident, generate the rule as if they did
  (Sentinel scheduled rules create incidents by default when `enabled: true`) and say so in `notes`.
