# Azure setup

Everything here is done once, by you, in your tenant. Work through it in order —
each step's verification proves the next one can work.

> **The single most common way to lose an afternoon on this integration:** the
> management plane and the Log Analytics query API are **different audiences**
> and need separately-scoped tokens. A token for `management.azure.com` returns
> 401 against `api.loganalytics.io`, and the error message does not say
> "wrong scope". Two credentials are required. See step 3.

---

## 1. Create the service principals

Two, deliberately. The deploy principal can write to your workspace; the query
principal is read-only and is the one handed to the sidecar, which executes
model-generated KQL.

```bash
SUB=<your-subscription-id>
RG=<your-resource-group>
WS=<your-log-analytics-workspace-name>

# Deploy principal — used by n8n
az ad sp create-for-rbac --name "sentinel-n8n-deploy" \
  --role "Log Analytics Contributor" \
  --scopes "/subscriptions/$SUB/resourceGroups/$RG"
# -> note appId (AZURE_CLIENT_ID), password (AZURE_CLIENT_SECRET), tenant

# Query principal — used by the sidecar, read-only
az ad sp create-for-rbac --name "sentinel-n8n-query" \
  --role "Log Analytics Reader" \
  --scopes "/subscriptions/$SUB/resourceGroups/$RG/providers/Microsoft.OperationalInsights/workspaces/$WS"
# -> note appId (AZURE_LOGS_CLIENT_ID), password (AZURE_LOGS_CLIENT_SECRET)
```

## 2. Grant the remaining roles to the deploy principal

`Log Analytics Contributor` covers `savedSearches` (parsers) but **not**
workbooks or analytic rules, which live under different resource providers.

```bash
DEPLOY_APP_ID=<appId from step 1>

# Workbooks: Microsoft.Insights/workbooks
az role assignment create --assignee "$DEPLOY_APP_ID" \
  --role "Monitoring Contributor" \
  --scope "/subscriptions/$SUB/resourceGroups/$RG"

# Analytic rules: Microsoft.SecurityInsights/alertRules
az role assignment create --assignee "$DEPLOY_APP_ID" \
  --role "Microsoft Sentinel Contributor" \
  --scope "/subscriptions/$SUB/resourceGroups/$RG"
```

| Resource | `resource_type` | Provider | Role that grants write |
|---|---|---|---|
| Parser | `savedSearch` | `Microsoft.OperationalInsights` | Log Analytics Contributor |
| Workbook | `workbook` | `Microsoft.Insights` | Monitoring Contributor |
| Analytic rule | `alertRule` | `Microsoft.SecurityInsights` | Microsoft Sentinel Contributor |

A missing role produces a clean `403` naming the role, not `HTTP 0` — see
Troubleshooting below.

## 3. Fill in `.env`

The **workspace id** is the GUID labelled *Workspace ID* on the workspace
Overview blade — not the ARM resource id.

```bash
az monitor log-analytics workspace show \
  -g "$RG" -n "$WS" --query customerId -o tsv
```

```ini
AZURE_TENANT_ID=...
AZURE_SUBSCRIPTION_ID=...
AZURE_RESOURCE_GROUP=...
AZURE_WORKSPACE_NAME=...
AZURE_LOCATION=eastus

# deploy principal
AZURE_CLIENT_ID=...
AZURE_CLIENT_SECRET=...

# workspace customerId GUID
AZURE_WORKSPACE_ID=...

# query principal (read-only)
AZURE_LOGS_CLIENT_ID=...
AZURE_LOGS_CLIENT_SECRET=...
```

Then `docker compose up -d` to pick the values up — n8n reads the non-secret
ones at start to build ARM resource ids.

## 4. Create the n8n credentials

n8n does not read secrets from `.env`; they go in its encrypted credential
store. In the n8n UI, **Credentials → Add credential**:

> **Why this can't just be `.env`.** `docker-compose.yml` only passes the
> *non-secret* Azure values (`AZURE_SUBSCRIPTION_ID`, `AZURE_RESOURCE_GROUP`,
> `AZURE_WORKSPACE_NAME`, `AZURE_LOCATION`) into the `n8n` container as plain
> env vars — those are just used to build the ARM resource path string, no
> auth involved. `AZURE_CLIENT_ID`/`AZURE_CLIENT_SECRET` are deliberately
> **not** passed through. The `GET Current Resource` and `PUT to Azure` nodes
> in *Sentinel — Deploy Component* authenticate via an n8n **OAuth2
> credential** object, not a Code node reading `$env`, so that: the secret is
> encrypted at rest by n8n's own instance key (never plaintext in `.env` or a
> container's env list); n8n handles the client-credentials token fetch and
> refresh automatically; and the workflow JSON — freely imported, exported,
> and committed to git — only ever carries a *reference* to the credential's
> id, never the secret itself. This is also why an exported workflow moved to
> a new VM always needs this step redone: the credential store is scoped to
> that n8n instance's own encryption key, and a fresh instance's store starts
> empty regardless of what's already in `.env`. A missing or unlinked
> credential fails **before n8n opens a socket** — no HTTP status is ever
> returned, which is why this specific misconfiguration shows up in chat as
> `HTTP 0`, not a real Azure error code.

### `Azure Management (client credentials)` — type **OAuth2 API**

| Field | Value |
|---|---|
| Grant Type | Client Credentials |
| Access Token URL | `https://login.microsoftonline.com/<TENANT_ID>/oauth2/v2.0/token` |
| Client ID | deploy principal appId |
| Client Secret | deploy principal password |
| Scope | `https://management.azure.com/.default` |
| Authentication | Body |

The *Deploy Component* workflow references this credential by name. If you
name it differently, reattach it on its HTTP nodes.

### `Sentinel Postgres` — type **Postgres**

Used by the orchestrator's chat memory.

| Field | Value |
|---|---|
| Host | `postgres` |
| Database | `n8n` |
| User | value of `POSTGRES_USER` |
| Password | value of `POSTGRES_PASSWORD` |
| Port | `5432` |

---

## 5. Verify — run these before trusting anything

### 5a. Both tokens issue

```bash
set -a; . ./.env; set +a

mgmt=$(curl -s -X POST \
  "https://login.microsoftonline.com/$AZURE_TENANT_ID/oauth2/v2.0/token" \
  -d grant_type=client_credentials -d "client_id=$AZURE_CLIENT_ID" \
  -d "client_secret=$AZURE_CLIENT_SECRET" \
  -d 'scope=https://management.azure.com/.default' | jq -r .access_token)

logs=$(curl -s -X POST \
  "https://login.microsoftonline.com/$AZURE_TENANT_ID/oauth2/v2.0/token" \
  -d grant_type=client_credentials -d "client_id=${AZURE_LOGS_CLIENT_ID:-$AZURE_CLIENT_ID}" \
  -d "client_secret=${AZURE_LOGS_CLIENT_SECRET:-$AZURE_CLIENT_SECRET}" \
  -d 'scope=https://api.loganalytics.io/.default' | jq -r .access_token)

[ ${#mgmt} -gt 100 ] && echo "mgmt token OK" || echo "mgmt token FAILED"
[ ${#logs} -gt 100 ] && echo "logs token OK" || echo "logs token FAILED"
```

### 5b. The query API answers

```bash
curl -s -X POST \
  "https://api.loganalytics.io/v1/workspaces/$AZURE_WORKSPACE_ID/query" \
  -H "Authorization: Bearer $logs" -H 'Content-Type: application/json' \
  -d '{"query":"Usage | take 1"}' | jq '.tables[0].rows | length'
```

`1` means the read path works. `401` means the token was minted with the wrong
scope; `403` means the principal has no role on the workspace.

### 5c. The two PUT shapes are right — **do this before writing any code that depends on them**

Against a scratch resource group, not production. This is the one step worth
doing by hand, because the workbook resource has three non-obvious
requirements at once: the name must be a **GUID**, `category` must be
`"sentinel"`, and `serializedData` is the workbook JSON **as a string**.

```bash
# Parser
curl -s -X PUT \
  "https://management.azure.com/subscriptions/$AZURE_SUBSCRIPTION_ID/resourceGroups/$AZURE_RESOURCE_GROUP/providers/Microsoft.OperationalInsights/workspaces/$AZURE_WORKSPACE_NAME/savedSearches/probe_parser?api-version=2020-08-01" \
  -H "Authorization: Bearer $mgmt" -H 'Content-Type: application/json' \
  -d '{"properties":{"category":"Microsoft Sentinel Parser","displayName":"Probe","functionAlias":"probe_parser","query":"Usage | take 1","version":2}}' \
  -w '\nHTTP %{http_code}\n' | tail -3

# Workbook — name MUST be a GUID
GUID=$(cat /proc/sys/kernel/random/uuid)
curl -s -X PUT \
  "https://management.azure.com/subscriptions/$AZURE_SUBSCRIPTION_ID/resourceGroups/$AZURE_RESOURCE_GROUP/providers/Microsoft.Insights/workbooks/$GUID?api-version=2023-06-01" \
  -H "Authorization: Bearer $mgmt" -H 'Content-Type: application/json' \
  -d "{\"location\":\"$AZURE_LOCATION\",\"kind\":\"shared\",\"properties\":{\"displayName\":\"Probe\",\"category\":\"sentinel\",\"serializedData\":\"{\\\"version\\\":\\\"Notebook/1.0\\\",\\\"items\\\":[]}\",\"sourceId\":\"/subscriptions/$AZURE_SUBSCRIPTION_ID/resourceGroups/$AZURE_RESOURCE_GROUP/providers/Microsoft.OperationalInsights/workspaces/$AZURE_WORKSPACE_NAME\"}}" \
  -w '\nHTTP %{http_code}\n' | tail -3
```

`200` or `201` on both means the deploy path will work. Clean up:

```bash
curl -s -X DELETE "https://management.azure.com/.../savedSearches/probe_parser?api-version=2020-08-01" -H "Authorization: Bearer $mgmt"
curl -s -X DELETE "https://management.azure.com/.../workbooks/$GUID?api-version=2023-06-01" -H "Authorization: Bearer $mgmt"
```

### 5d. The sidecar agrees

```bash
docker compose exec n8n wget -qO- http://sentinel-agent:8000/healthz
```

`"azure_configured": true` means the sidecar found its credentials and KQL
validation is live rather than skipped.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| Deploy fails with `HTTP 0` (chat shows `GET before deploy failed with HTTP 0. undefined`, or the newer message naming this explicitly) | No HTTP response was ever received — a transport/credential problem, not an Azure rejection. Almost always: the `Azure Management (client credentials)` credential (§4) doesn't exist yet on this n8n instance, or exists but isn't attached to **both** `GET Current Resource` and `PUT to Azure` in *Sentinel — Deploy Component*. n8n resolves the credential before opening a socket, so a missing one never reaches Azure at all. Re-check §4, including after any workflow re-import (imports reset credential links). |
| `401` on the query API, `200` on management | Token minted with the management scope. They are different audiences — mint two. |
| `403` on a workbook PUT, parsers fine | `Log Analytics Contributor` does not cover `Microsoft.Insights`. Add **Monitoring Contributor**. |
| `403` on an analytic rule PUT, parsers/workbooks fine | Neither `Log Analytics Contributor` nor `Monitoring Contributor` covers `Microsoft.SecurityInsights`. Add **Microsoft Sentinel Contributor** (§2). |
| `"incorrect segment lengths"` | An ARM-template naming bug. It cannot occur on the direct PUT path used here — if you see it, something is deploying via an ARM template. |
| `409 Conflict` on deploy | The resource changed between the GET and the PUT. Run `deploy_draft` again; do not force. |
| `"azure_configured": false` | `AZURE_TENANT_ID`, `AZURE_WORKSPACE_ID`, or the query credentials are unset. Restart the sidecar after editing `.env`. |
| Workbook deploys but does not appear in Sentinel | `category` is not `"sentinel"`, or `sourceId` is not the workspace ARM id. |
