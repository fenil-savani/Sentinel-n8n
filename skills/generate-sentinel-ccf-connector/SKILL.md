---
name: generate-sentinel-ccf-connector
description: Generate a Microsoft Sentinel Codeless Connector Framework (CCF) v2 RestApiPoller
  data connector file set — ConnectorDefinition, PollerConfig, DCR, and (for custom tables)
  Table — from an API description, TDD, or sample response. Use this whenever the user wants a
  new Sentinel data connector generated for a REST API vendor, phrased as "generate a CCF
  connector", "build a codeless connector", or "create a data connector for <vendor>".
---

# Generate a Microsoft Sentinel CCF v2 connector (RestApiPoller)

A CCF v2 connector is **four separate JSON files**, cross-referenced by exact string matches.
This version of the skill covers **`RestApiPoller` only** — polling a REST API. WebSocket, GCP,
AmazonWebServicesS3, StorageAccountBlobContainer, and Push are out of scope for now; if the
vendor needs one of those, stop and say so via `request_input` rather than improvising.

| # | File | Resource Type | Always Required? |
|---|------|---------------|-------------------|
| 1 | `_ConnectorDefinition.json` | `Microsoft.SecurityInsights/dataConnectorDefinitions` | Yes |
| 2 | `_PollerConfig.json` | `Microsoft.SecurityInsights/dataConnectors` | Yes |
| 3 | `_DCR.json` | `Microsoft.Insights/dataCollectionRules` | Yes |
| 4 | `_Table.json` | `Microsoft.OperationalInsights/workspaces/tables` | Only for a custom `_CL` table |

**You never hallucinate an API property.** If the auth type, pagination style, or a field's
type is genuinely unknown, call `request_input` rather than guessing — a wrong guess in an auth
block or a stream name is worse than an honest gap, and either one silently breaks V3
packaging or the live connection.

---

## Step 1 — Reference examples: read one before writing anything

Seven real, production connectors are available under `read_reference` (paths shown are
relative to the reference root, e.g. `ccf_connector/GitHub/GitHubAuditLogs_ccf/...`). Call
`list_reference_files` to see exact filenames, then read the **PollerConfig/PollingConfig**
file of whichever vendor below is closest to this connector's auth + pagination combination —
mirror its structure, not just its shape:

| Auth | Pagination | Mirror this vendor | Notes |
|------|-----------|--------------------|-------|
| APIKey | LinkHeader | `ccf_connector/GitHub/` | header-based key, `Link:` response header |
| APIKey | None, many pollers sharing one ConnectorDefinition | `ccf_connector/Netskopev2/` | 20-poller one-to-many pattern |
| Basic | Offset | `ccf_connector/AtlassianJiraAudit/` | `UserName`/`Password` |
| OAuth2 (form body) | None | `ccf_connector/Auth0/` | simplest OAuth2 case |
| OAuth2 (form body) | Offset | `ccf_connector/Workday/` | **no Table.json** — standard-table pattern, read this if the data maps to an existing standard table |
| OAuth2 | PersistentToken | `ccf_connector/Box/` | cursor persists across polling runs |
| OAuth2 | NextPageToken | `ccf_connector/Sophos Endpoint Protection/` | 2 pollers, one ConnectorDefinition |
| OAuth2, multi-step / multi-poller | Offset + NextPageToken | `ccf_connector/CrowdStrike Falcon Endpoint Protection/` | 5 pollers, most complex real example here |

No real example uses `JwtToken` auth — if that's what this vendor needs, follow the JwtToken
spec in Step 5 below directly; there's nothing to mirror for it.

---

## Step 2 — Required inputs

Before generating, you need (ask via `request_input` for anything missing — never guess):

| Input | Notes |
|-------|-------|
| Company name | PascalCase, used in folder/file naming |
| Product name | PascalCase, used in folder/file naming |
| Log type | e.g. "Logs", "Events", "Alerts", "AuditLogs" |
| Publisher name | shown in the Sentinel UI |
| Auth type | APIKey / Basic / OAuth2 / JwtToken |
| API endpoint(s) | full URL(s) |
| API response structure | field names + types, or a sample response |
| Pagination type | Offset / NextPageToken / PersistentToken / LinkHeader / None |
| Table | existing standard table name, or confirmation a custom `_CL` table is needed |

---

## Step 3 — Folder & file naming

**Folder**: `{CompanyName}{ProductName}{LogType}Logs_ccp` — PascalCase, no spaces/hyphens/
underscores between components, suffix always `_ccp` (lowercase). Example:
`PaloAltoPrismaCloudCWPPLogs_ccp`.

**Files** (all under that folder, or at the root of `Data Connectors/` for a one-to-many
ConnectorDefinition shared across subfolders):

| File | Pattern |
|------|---------|
| Connector Definition | `{CompanyName}{ProductName}{LogType}_ConnectorDefinition.json` |
| Poller Config | `{CompanyName}{ProductName}{LogType}_PollerConfig.json` |
| DCR | `{CompanyName}{ProductName}{LogType}_DCR.json` |
| Table | `{CompanyName}{ProductName}{LogType}_Table.json` (omit for a standard table) |

These names are for your own reference and for the `notes` you hand back — `submit_ccf_connector`
takes each file's JSON content directly; the harness writes the actual files to disk.

**API versions — use exactly these, no others:**

| Resource | API Version |
|----------|-------------|
| `dataConnectorDefinitions` | `2022-09-01-preview` |
| `dataConnectors` (RestApiPoller) | `2023-12-01-preview` |
| `dataCollectionRules` | `2021-09-01-preview` |
| `workspaces/tables` | `2021-03-01-privatepreview` |

---

## Step 4 — File structures

### 4a. ConnectorDefinition

```json
{
  "name": "<ConnectorDefinitionId>",
  "apiVersion": "2022-09-01-preview",
  "type": "Microsoft.SecurityInsights/dataConnectorDefinitions",
  "location": "{{location}}",
  "kind": "Customizable",
  "properties": {
    "connectorUiConfig": {
      "id": "<ConnectorDefinitionId>",
      "title": "<Display Name> (via Codeless Connector Framework)",
      "publisher": "<Publisher Name>",
      "descriptionMarkdown": "<Markdown description>",
      "graphQueriesTableName": "<PrimaryTable_CL>",
      "graphQueries": [{ "metricName": "Total events received", "legend": "<Product> Events", "baseQuery": "{{graphQueriesTableName}}" }],
      "sampleQueries": [
        { "description": "All <Product> logs", "query": "{{graphQueriesTableName}}\n| sort by TimeGenerated desc" },
        { "description": "Total events by hour", "query": "{{graphQueriesTableName}}\n| summarize count() by bin(TimeGenerated, 1h)" }
      ],
      "dataTypes": [{ "name": "{{graphQueriesTableName}}", "lastDataReceivedQuery": "{{graphQueriesTableName}}\n|summarize Time = max(TimeGenerated)\n|where isnotempty(Time)" }],
      "connectivityCriteria": [{ "type": "HasDataConnectors" }],
      "availability": { "status": 1, "isPreview": false },
      "permissions": {
        "resourceProvider": [{
          "provider": "Microsoft.OperationalInsights/workspaces",
          "permissionsDisplayText": "Read and Write permissions are required.",
          "providerDisplayName": "Workspace", "scope": "Workspace",
          "requiredPermissions": { "write": true, "read": true, "delete": true, "action": false }
        }],
        "customs": [{ "name": "<Product> API Key", "description": "A valid <Product> API key is required. Navigate to <Settings Location> to generate one." }]
      },
      "instructionSteps": [ "... see Step 5's UI patterns below ..." ]
    }
  }
}
```

Mandatory: `kind` is always `"Customizable"`. `name` and `connectorUiConfig.id` are IDENTICAL
with no spaces — this is the single most common packaging failure. Minimum 2 `sampleQueries`.
`"type": "password"` for every secret/API-key field in `instructionSteps`; `"type": "text"` for
everything else.

**UI instruction step, by auth type:**
- **APIKey/Basic (single account)** — one `Textbox` (type `password` for the secret) +
  `ConnectionToggleButton`.
- **OAuth2 client_credentials** — still `Textbox(password)` + `ConnectionToggleButton`, NOT
  `OAuthForm`. `OAuthForm` is only for OAuth2 *authorization_code* flows.
- **Multiple accounts/tenants** — `DataConnectorsGrid` + `ContextPane`.
- **Optional per-log-type opt-in** (Netskope/CrowdStrike-style) — add a `Dropdown`.

### 4b. PollerConfig

```json
[{
  "name": "<DescriptivePollerName>",
  "apiVersion": "2023-12-01-preview",
  "type": "Microsoft.SecurityInsights/dataConnectors",
  "location": "{{location}}",
  "kind": "RestApiPoller",
  "properties": {
    "connectorDefinitionName": "<MustMatchConnectorDefinitionId>",
    "dataType": "<TableName_CL>",
    "dcrConfig": { "streamName": "Custom-<TableName_CL>" },
    "auth": { "...": "see Step 5" },
    "request": {
      "apiEndpoint": "https://api.example.com/v1/events",
      "httpMethod": "GET",
      "retryCount": 3,
      "timeoutInSeconds": 60,
      "queryTimeFormat": "yyyy-MM-ddTHH:mm:ssZ",
      "queryWindowInMin": 5,
      "headers": { "Accept": "application/json", "User-Agent": "Scuba" }
    },
    "response": { "eventsJsonPaths": ["$.data"], "format": "json" },
    "paging": { "...": "see Step 6, omit entirely if the API has no pagination" }
  }
}]
```

Always include `retryCount: 3`, `timeoutInSeconds: 60`, `headers["User-Agent"] = "Scuba"`,
`response.format = "json"`. Common `eventsJsonPaths`: `$.records`, `$.data`, `$.items`,
`$.results`, `$.events`, `$[*]` (root array), or `""` for the full raw response.

**Time filter** — pick ONE:
- Named attributes: `"startTimeAttributeName": "since", "endTimeAttributeName": "until"`
  (auto-appended as query params)
- Explicit params: `"queryParameters": {"start_time": "{_QueryWindowStartTime}", "end_time": "{_QueryWindowEndTime}"}`
- POST body template: `"httpMethod": "POST", "queryParametersTemplate": "{ 'filters': [...{_QueryWindowStartTime}...] }"`

**`queryTimeFormat`** — ISO 8601 with `Z` → `"yyyy-MM-ddTHH:mm:ssZ"`; ISO 8601 with microseconds
→ `"yyyy-MM-ddTHH:mm:ss.000000+00:00"`; Unix seconds → `"UnixTimestamp"`; Unix milliseconds →
`"UnixTimestampInMills"`; date only → `"yyyy-MM-dd"`.

**Multi-poller array rules** (Netskope/Sophos/CrowdStrike-style, one endpoint per log type):
every poller `name` unique, every poller shares the SAME `connectorDefinitionName`, each has
its own `streamName`. Optional per-poller opt-in: `"condition": "[[equals(parameters('enableLogType')[0], 'Yes')]"`.

### 4c. DCR

```json
[{
  "name": "<ShortDCRNameMax65Chars>",
  "apiVersion": "2021-09-01-preview",
  "type": "Microsoft.Insights/dataCollectionRules",
  "location": "{{location}}",
  "properties": {
    "dataCollectionEndpointId": "{{dataCollectionEndpointId}}",
    "streamDeclarations": {
      "Custom-<streamName>": { "columns": [{ "name": "field1", "type": "string" }, { "name": "timestamp", "type": "long" }] }
    },
    "destinations": { "logAnalytics": [{ "workspaceResourceId": "{{workspaceResourceId}}", "name": "clv2ws1" }] },
    "dataFlows": [{
      "streams": ["Custom-<streamName>"],
      "destinations": ["clv2ws1"],
      "transformKql": "source | extend TimeGenerated = now()",
      "outputStream": "Custom-<TableName_CL>"
    }]
  }
}]
```

Critical: `name` ≤ **65 characters** (count them explicitly — this is a hard deploy-time limit,
not a style preference), no spaces. `streamDeclarations` key and `dcrConfig.streamName` (from
the PollerConfig) must be byte-identical, `Custom-` prefix included. Exactly ONE stream per
`dataFlows` entry. `destinations` name is always `"clv2ws1"`. `outputStream` present for a
custom table, omitted (or `Microsoft-` prefixed) when targeting a standard table.

`streamDeclarations` columns are snake_case matching the raw API response. Types: `string`,
`datetime`, `int`, `long` (also for Unix timestamps — convert to `datetime` in KQL), `boolean`,
`dynamic` (objects/arrays), `guid`, `real`.

### 4d. Table (custom `_CL` tables only)

```json
[{
  "name": "<ProductName>_<DataType>_CL",
  "type": "Microsoft.OperationalInsights/workspaces/tables",
  "apiVersion": "2021-03-01-privatepreview",
  "properties": {
    "schema": {
      "name": "<ProductName>_<DataType>_CL",
      "columns": [
        { "name": "TimeGenerated", "type": "datetime", "isDefaultDisplay": false, "isHidden": false },
        { "name": "EventVendor", "type": "string", "isDefaultDisplay": false, "isHidden": false },
        { "name": "EventProduct", "type": "string", "isDefaultDisplay": false, "isHidden": false }
      ]
    }
  }
}]
```

`name` and `properties.schema.name` are IDENTICAL. Neither contains `Custom-`. Both end in
`_CL`. Every column the KQL transform outputs must be declared here — no more, no fewer.
`TenantId` is auto-added by Azure; never declare it. PascalCase column names, analyst-friendly
(not the raw snake_case from `streamDeclarations`).

**Skip this file entirely** when the data maps exactly to a Microsoft standard table (see the
Workday reference example) — omit `outputStream` from the DCR too, or prefix it `Microsoft-`.

---

## Step 5 — Authentication blocks

**APIKey** — header-based:
```json
"auth": { "type": "APIKey", "ApiKey": "{{apikey}}", "ApiKeyName": "<HeaderName>", "IsApiKeyInPostPayload": false }
```

**Basic**:
```json
"auth": { "type": "Basic", "UserName": "{{userid}}", "Password": "{{password}}" }
```

**OAuth2, credentials in form body** (the common case — Auth0/Box/Workday-style):
```json
"auth": {
  "type": "OAuth2", "ClientId": "{{clientid}}", "ClientSecret": "{{clientsecret}}",
  "TokenEndpoint": "{{tokenEndpointUrl}}", "GrantType": "client_credentials"
}
```

**OAuth2, token endpoint requires HTTP Basic** (`Authorization: Basic base64(id:secret)` —
check the vendor's API spec for which one they use):
```json
"auth": {
  "type": "OAuth2", "TokenEndpoint": "{{tokenEndpointUrl}}", "GrantType": "client_credentials",
  "TokenEndpointHeaders": { "Authorization": "[[concat('Basic ', base64(concat(parameters('clientid'), ':', parameters('clientsecret'))))]" }
}
```

**OAuth2 critical rules**: `TokenEndpointQueryParameters` must NEVER contain any of `client_secret`,
`client_id`, `code`, `grant_type`, `redirect_uri`, `scope`, `apikey` — these break the connector
with `BadRequest: OAuth2 config error`. If no other extra params are needed, omit the
`TokenEndpointQueryParameters` block entirely rather than leaving it empty.

**JwtToken** (no real reference example — follow this spec directly):
```json
"auth": {
  "type": "JwtToken", "UserToken": "{{initialToken}}", "UserTokenPrepend": "",
  "TokenEndpoint": "https://{{fqdn}}/api/v1/refresh-access-token",
  "TokenEndpointHttpMethod": "GET", "NoAccessTokenPrepend": true,
  "JwtTokenJsonPath": "$.systemToken"
}
```

---

## Step 6 — Pagination blocks

Omit the `paging` block entirely if the API has no pagination (Auth0/Netskope-style).

**Offset** (page numbers):
```json
"paging": { "pagingType": "Offset", "offsetParaName": "offset", "pageSizeParaName": "limit", "pageSize": 100 }
```

**NextPageToken** (opaque token, valid for the next page only):
```json
"paging": { "pagingType": "NextPageToken", "nextPageTokenJsonPath": "$.next_cursor", "nextPageParaName": "cursor" }
```
Add `"hasNextFlagJsonPath": "$.has_more"` if the API also returns a boolean "has more" flag.

**PersistentToken** (cursor persists ACROSS polling runs — streaming/incremental):
```json
"paging": { "pagingType": "PersistentToken", "nextPageTokenJsonPath": "$.next_cursor", "NextPageParaName": "cursor" }
```

**LinkHeader** (RFC 5988):
```json
"paging": { "pagingType": "LinkHeader" }
```
Add `"linkHeaderTokenJsonPath": "$.next_url"` only if the next-page URL is in the JSON body
instead of the HTTP `Link:` response header.

---

## Step 7 — KQL transform (`transformKql`), 4-phase algorithm

```kql
source
// Phase 1: TimeGenerated + static vendor/product labels
| extend TimeGenerated = <conversion>, EventVendor = "<Vendor>", EventProduct = "<Product>"
// Phase 2: flatten nested dynamic fields
| extend NestedField = nested_obj.child_key
// Phase 3: type-cast extracted scalars
| extend NestedField = tostring(NestedField)
// Phase 4: remove raw nested objects, rename reserved words
| project-away nested_obj
| project-rename EventId = id_s, EventType = type_s
```

**`TimeGenerated` MUST be set in every `transformKql` — this is a hard auto-fail if missing.**
Conversion by source format: ISO 8601 string → `extend TimeGenerated = datetime_field`; Unix
seconds → `datetime(1970-01-01) + field * 1sec`; Unix milliseconds →
`datetime(1970-01-01) + (field * 1ms)`; no usable timestamp → `now()`.

**Reserved KQL keywords** — never use as a final column name without renaming:
`type`, `count`, `title`, `id`, `status`, `class`, `level`, `timestamp`. Rename via
`project-rename` (e.g. `EventId = id_s`) or a suffix (`_s`/`_l`/`_i`/`_b`).

KQL output columns must exactly match the Table schema columns — no missing, no extra.

---

## Step 8 — Cross-file mapping chain (verify yourself before submitting)

```
ConnectorDefinition.connectorUiConfig.id  ==  PollerConfig.properties.connectorDefinitionName
PollerConfig.properties.dcrConfig.streamName  ==  DCR.properties.streamDeclarations key  (e.g. "Custom-MyStream")
DCR.properties.dataFlows[*].outputStream  ==  "Custom-" + Table.name  (Table.name has NO "Custom-" prefix)
Table.name  ==  Table.properties.schema.name
```

Every one of these must be a byte-identical string match. A mismatch anywhere in this chain is
the single most common CCF packaging/connection failure — check it yourself before calling
`submit_ccf_connector`; a deterministic check runs after submission too, but catching it
yourself first saves a revision round trip.

---

## DOs and DON'Ts

**DO**: set `TimeGenerated` in every transform · use `now()` when no real timestamp exists ·
convert Unix seconds with `* 1sec`, milliseconds with `* 1ms` · declare every ingested column in
`streamDeclarations` · use `"dynamic"` for nested JSON · use `"long"` for Unix timestamps in
`streamDeclarations` · include `User-Agent: "Scuba"` on every request · keep `queryWindowInMin`
at 5 unless volume says otherwise · include `"format": "json"` in every response block · keep
the DCR name under 65 characters, counted explicitly · use PascalCase for final table columns,
snake_case in `streamDeclarations` · set `isPreview: false` / `status: 1` for GA connectors.

**DON'T**: hardcode subscription IDs, resource group names, workspace names, or any secret
value (use `{{placeholder}}` syntax throughout) · use a KQL reserved word as a final column name
without renaming · put `Custom-` in the Table file's `name`/`schema.name` · skip
`dataCollectionEndpointId` in the DCR · let Poller and DCR stream names differ in any way · omit
`retryCount`/`timeoutInSeconds` · use `"string"` for a credential ARM parameter (mentally reserve
`"securestring"` even though this generation only produces `{{placeholder}}` files) · declare a
table column the KQL transform doesn't actually produce · add `_CL` to an individual column name
· put more than one stream in a single `dataFlows[*].streams` entry · generate a CLv1 connector
(a single file with both `connectorUiConfig` and `pollerConfig`) — always CLv2, separate files ·
include a reserved OAuth2 param (`grant_type`, `client_id`, `client_secret`, `code`,
`redirect_uri`, `scope`, `apikey`) in `TokenEndpointQueryParameters` · leave
`logResponseContent: true` set.

---

## Submitting

Call `submit_ccf_connector` exactly once, with the complete JSON content of each file as its
own string field (`connector_definition_json`, `poller_config_json`, `dcr_json`, and
`table_json` if a custom table is needed) — no markdown fences, no commentary. A deterministic
check then verifies the cross-file mapping chain and the rules above; if it fails, you'll see
exactly which rule and can fix it without starting over.
