# Sentinel Component Builder

Chat with an agent to generate Microsoft Sentinel **TDDs**, **parsers**,
**analytic rules**, and **workbooks**, and deploy them straight into your
workspace — with a snapshot, a diff, and an explicit confirmation in front of
every write.

The generation rules live in one file per module under `skills/`, which are
the single source of truth for both humans and the agent:

- [skills/generate-sentinel-tdd/SKILL.md](skills/generate-sentinel-tdd/SKILL.md)
- [skills/generate-sentinel-parser/SKILL.md](skills/generate-sentinel-parser/SKILL.md)
- [skills/generate-sentinel-analytic-rule/SKILL.md](skills/generate-sentinel-analytic-rule/SKILL.md)
- [skills/generate-sentinel-workbook/SKILL.md](skills/generate-sentinel-workbook/SKILL.md)

Edit any one of them and the next generation picks it up — no rebuild, no
second copy.

---

## Architecture

```
Analyst ── your @n8n/chat portal (or the n8n editor's own chat panel)
   │
   ▼
Chat Trigger ──> Orchestrator agent  (Claude, via sentinel-agent's /v1 proxy)
   │
   ├─ generate_tdd ───────────┐
   ├─ generate_parser ────────┤
   ├─ generate_workbook ──────┤
   ├─ generate_analytic_rule ─┤
   ├─ validate_kql ───────────┼─ each is its own tiny n8n sub-workflow
   ├─ lint_draft ─────────────┤  ("Sentinel — Tool: <name>") that calls
   ├─ describe_draft ─────────┤  sentinel-agent (sidecar) over HTTP
   ├─ list_drafts ────────────┤
   ├─ revise_draft ───────────┘
   │
   └─ deploy_draft ──> Sentinel — Deploy Component   GET-check, then direct PUT to Azure

Postgres: n8n's own state │ sentinel drafts + deployments (separate database, same server)
```

Four design decisions carry most of the weight:

**Artifacts never enter the model's context.** A workbook can be 180 KB. Every
generator writes the artifact to Postgres *and* to a shared `./output` folder
laid out like a real Azure-Sentinel solution repo, and returns a `draft_id`
plus a small summary; the orchestrator reasons over the summary and tells the
analyst where the real file is. This is also why an approval turn hours after
generation still works — nothing depends on chat memory.

**Every tool is its own sub-workflow, not an inline HTTP-request tool node.**
n8n 2.35.6's `toolHttpRequest` node (the "HTTP Request Tool" you'd normally
wire directly into an agent) has a real bug in this version — it can crash
mid-conversation with `has a "supplyData" method but no "execute" method"`.
Every tool here is instead a one-node "Execute Workflow"-style sub-workflow
(`n8n/workflows/sentinel-tool-*.json`), the same pattern `deploy_draft` already
used. See **Known n8n platform quirks** below before touching this.

**Workbooks are assembled by code, not by the model.** The model plans panels,
then writes one ~1 KB query object per panel. Every structural field — envelope,
groups, parameters, borders, refresh buttons, grid settings — comes from
templates in [`panel_templates.py`](services/sentinel-agent/app/workbook/panel_templates.py),
extracted from the shipped `CorelightDataExplorer.yaml`.

**The model never authors an Azure write.** Its authority stops at
`deploy_draft(draft_id)`. Deterministic code in the deploy workflow builds the
Azure request from the stored artifact and PUTs it — the model only ever names
which draft to deploy.

---

## Setup

Follow this in order on a genuinely fresh environment — and again, from Step 4
onward, any time you run `docker compose down -v` (which wipes n8n's
credential store along with everything else).

### 1. Prerequisites

- Docker + Docker Compose.
- One LLM path for `sentinel-agent`'s own generation calls, decided now (you
  can switch later by editing `.env` and recreating the container):
  - **Anthropic API key** (`LLM_PROVIDER=anthropic`) — from
    console.anthropic.com, billed per token.
  - **Claude CLI / Claude Code subscription** (`LLM_PROVIDER=claude_cli`, no
    API key) — needs a machine already logged in via `claude` (check with
    `claude auth status`). See **Claude CLI trade-offs** below — in
    particular, this backend is noticeably slower per call (it shells out to
    a real CLI process) and shares whatever rate limit your subscription has,
    so concurrent testing can produce `529 Overloaded`/`503` responses that
    have nothing to do with the workflow itself. Retry after a pause.

This is independent of the **n8n orchestrator's own chat model**, configured
in Step 4 below — it always talks to `sentinel-agent`'s own OpenAI-compatible
proxy, regardless of which `LLM_PROVIDER` you pick here.

### 2. Configure `.env`

```bash
cp .env.example .env
openssl rand -hex 32   # use the output for N8N_ENCRYPTION_KEY — see warning below
```

Edit `.env` and fill in at minimum:
- `POSTGRES_PASSWORD`, `N8N_ENCRYPTION_KEY`.

> **`N8N_ENCRYPTION_KEY` is generate-once, not regenerate-whenever.** n8n
> writes it into `/home/node/.n8n/config` inside the `n8n_data` volume the
> first time it ever starts, and encrypts every stored credential with it.
> Only run `openssl rand -hex 32` for this on a genuinely first-ever setup —
> once `n8n_data` exists, changing `.env`'s value without also updating (or
> wiping) that volume makes n8n refuse to start with `Mismatching encryption
> keys`. If that happens: restore the original key in `.env` if you still
> have it (zero data loss), or accept losing n8n's stored workflows/
> credentials/executions and remove the `n8n_data` volume to reinitialize
> fresh — `sentinel-agent`'s own Postgres data (drafts, deployments) lives in
> a separate database on the same Postgres server and is unaffected either
> way (removing `postgres_data` instead/also wipes that).
- Either `ANTHROPIC_API_KEY` (leave `LLM_PROVIDER=anthropic`), or set
  `LLM_PROVIDER=claude_cli` and run `claude setup-token` on a machine already
  logged in to Claude Code, pasting the result into `CLAUDE_CODE_OAUTH_TOKEN`.
- `LLM_MODEL_PARSER` / `LLM_MODEL_WORKBOOK_MANIFEST` / `LLM_MODEL_PANEL` —
  sensible defaults are already set; override only if you want a different
  model for one specific flow.
- Azure variables can be left blank for now — the stack runs without them,
  with KQL validation degrading to a documented skip. Fill them in before
  Step 8 below.

### 3. Start the stack

```bash
chmod o+w output   # sentinel-agent writes here as uid 10001, not your host user — see note below
docker compose up -d
docker compose logs -f sentinel-agent
```

> **Why the `chmod`:** `sentinel-agent` runs as a fixed unprivileged uid
> (`10001`, deliberately sandboxed — it also executes model-generated
> `run_python` code) that doesn't match your host user, so a freshly
> checked-out `output/` (owner-only write) blocks it from creating files.
> Without this, generation calls still succeed (drafts are durably stored in
> Postgres regardless) but silently fail to also write the file into
> `output/` — you'd see `could not write output file: [Errno 13] Permission
> denied` in `sentinel-agent`'s logs and a `file_error` field in the draft's
> summary, with nothing landing on disk.

Confirm a line like `ready: provider=anthropic parser_model=claude-opus-5
manifest_model=claude-opus-5 panel_model=claude-sonnet-5 azure=False`. If you
see `Error in sub-node ...` from `docker compose logs -f n8n` right now, that's
expected — no n8n credentials exist yet. Continue to Step 4.

### 4. Create the n8n owner account

Open **http://localhost:5678** (or your configured `N8N_HOST`/`N8N_PORT`) in a
browser. On first run n8n asks you to create the owner account — email,
name, password. Do that now; everything below assumes you're logged in.

### 5. Create the three n8n credentials (UI steps)

**Importing a workflow file does not create its credentials** — a node's
credential reference is just an id baked into the JSON until you link a real
credential to it. In the n8n editor's left sidebar, click **Credentials**,
then **Add credential** for each of the three below.

#### 5a. `Sentinel Postgres` — type **Postgres**

Used by the orchestrator's chat memory (so it remembers earlier turns in the
same session).

1. Credentials → Add credential → search "Postgres" → select it.
2. Fill in:
   | Field | Value |
   |---|---|
   | Host | `postgres` |
   | Database | `n8n` (the value of `.env`'s `POSTGRES_DB`) |
   | User | value of `.env`'s `POSTGRES_USER` |
   | Password | value of `.env`'s `POSTGRES_PASSWORD` |
   | Port | `5432` |
   | SSL | Disable |
3. Click **Save**, then **Test connection** — it should succeed since
   `postgres` is only reachable from inside the compose network you're
   already running in.

#### 5b. `OpenAI account` — type **OpenAI**

This is the orchestrator's own chat model. It is **not** a native Anthropic
credential, even though the node is named "Anthropic Chat Model" in the
canvas — that node is actually an *OpenAI Chat Model* node type pointed at
`sentinel-agent`'s own OpenAI-compatible proxy (`app/llm/openai_compat.py`),
which runs the real request through Claude (Anthropic API or the Claude CLI,
per `LLM_PROVIDER`) on the backend. Do not create a native "Anthropic"
credential for this node — it will not be read.

1. Credentials → Add credential → search "OpenAi" → select **OpenAi**.
2. Fill in:
   | Field | Value |
   |---|---|
   | API Key | any non-empty placeholder, e.g. `sk-local-proxy-unused` — the proxy never checks it |
   | Base URL | `http://sentinel-agent:8000/v1` |
   | Organization ID | leave blank |
3. Click **Save**. n8n's "Test" button on this credential type calls
   `GET /v1/models`, which the proxy answers directly — it should pass.

#### 5c. `Azure Management (client credentials)` — type **OAuth2 API**

Only needed once you're ready to actually deploy. See
[docs/azure-setup.md](docs/azure-setup.md) §4 for how to obtain the values.

1. Credentials → Add credential → search "OAuth2" → select **OAuth2 API**.
2. Fill in:
   | Field | Value |
   |---|---|
   | Grant Type | Client Credentials |
   | Access Token URL | `https://login.microsoftonline.com/<TENANT_ID>/oauth2/v2.0/token` |
   | Client ID | deploy principal appId |
   | Client Secret | deploy principal password |
   | Scope | `https://management.azure.com/.default` |
   | Authentication | Send as Body |
3. Click **Save**.

### 6. Import and activate the workflows

```bash
docker compose exec n8n n8n import:workflow --separate --input=/workflows
```

This creates (or updates) all 11 workflows: **Sentinel — Orchestrator**,
**Sentinel — Deploy Component**, and nine **Sentinel — Tool: `<name>`**
sub-workflows (one per tool — see Architecture above). The import marks
changed workflows inactive; reactivate every one of them:

```bash
for id in sentinel-orchestrator sentinel-deploy \
          sentinel-tool-generate-tdd sentinel-tool-generate-parser \
          sentinel-tool-generate-workbook sentinel-tool-generate-analytic-rule \
          sentinel-tool-validate-kql sentinel-tool-lint-draft \
          sentinel-tool-describe-draft sentinel-tool-list-drafts \
          sentinel-tool-revise-draft; do
  docker compose exec n8n n8n publish:workflow --id="$id"
done
docker compose restart n8n
```

> **Use `publish:workflow`, not `update:workflow --active=true`.** The latter
> is deprecated and, in this n8n version, can leave a workflow's `active`
> flag set without a corresponding *active version* record — the webhook then
> fails immediately with `Active version not found for workflow with id
> "..."`. `publish:workflow` is the command that actually creates that
> record. Either way, **changes only take effect after an n8n restart** —
> both commands print a reminder to that effect.

Then in the editor, open **Sentinel — Orchestrator**, click into the
**Postgres Chat Memory** and **Anthropic Chat Model** nodes, and re-pick the
credentials from Step 5 in their dropdowns (a fresh import resets a node's
credential selection back to an unlinked id) — then **save the workflow**
(the node panel and the workflow have separate saves; closing the node panel
alone doesn't persist it). Do the same for **GET Current Resource** and
**PUT to Azure** in **Sentinel — Deploy Component** once you've done Step 5c.

> **If you edit a workflow JSON file** (e.g. `n8n/workflows/orchestrator.json`'s
> system prompt), editing it on disk has no effect on the running instance —
> re-run the `import:workflow` command above, `publish:workflow` on whichever
> ids changed, restart n8n, and re-check credentials/activation, same as a
> fresh import.

### 7. Verify

```bash
docker compose exec sentinel-agent python -c \
  "import urllib.request, json; print(json.load(urllib.request.urlopen('http://localhost:8000/healthz')))"
```

Confirm `llm_provider`, `parser_model`, `workbook_manifest_model`, and
`panel_model` match what you set in `.env`. `sentinel-agent` isn't published to
the host (see **Security notes**), so `/healthz` is only reachable from inside
the compose network, as above.

Then send one real message to prove the whole chain end to end:

```bash
curl -sS -X POST "http://<N8N_HOST>:<N8N_PORT>/webhook/sentinel-chat/chat" \
  -H "Content-Type: application/json" \
  -d '{"chatInput": "generate a parser for vendor Acme, log type widgetlog, table Acme_widgetlog_CL", "sessionId": "smoke-test-1"}'
```

A working reply names a `draft_id` and, once validated, a file path under
`output/`. If it instead reports a tool error, re-check Step 5's credential
linking and Step 6's activation before assuming the workflow logic is wrong.

### 8. First real generation

Open the Chat panel on **Sentinel — Orchestrator** (or use your own portal
against the webhook above), ask for the Corelight conn parser, and diff the
result against `data/corelight_conn.yaml` — that diff is the real quality
signal, not anything in this doc.

### 9. Azure + deploy (when ready)

Follow [docs/azure-setup.md](docs/azure-setup.md) end to end, including §5c's
`curl` checks against a scratch resource group *before* trusting the deploy
path. Then: deploy → confirm it's callable in Sentinel Logs → redeploy with a
change → confirm it updates the same resource rather than duplicating it.

### Known n8n platform quirks (2.35.6 self-hosted)

Found the hard way — by tracing actual n8n/`@n8n/n8n-nodes-langchain` source
and Postgres execution records, not by guessing. Worth knowing before you add
a tenth tool or touch the agent wiring:

- **`toolHttpRequest` can crash a live tool call.** `has a "supplyData"
  method but no "execute" method` — this is why every tool here is a
  one-node sub-workflow instead. Don't add a new tool as a direct
  `toolHttpRequest` node without expecting this.
- **`$fromAI()` embedded in a `toolWorkflow` node's `workflowInputs` mapping
  is unreliable** — it can deliver the declared fields as all-`null`, or
  (with more than a couple of fields) silently degrade the tool to a
  freeform single-string argument instead of a structured one. Every tool
  sub-workflow here is written to expect **either**: it declares one input
  field, `query`, and its first real node does
  `typeof $json.query === 'string' ? JSON.parse($json.query) : $json.query`
  before using anything. The calling node's `description` field spells out
  the exact JSON shape the model should send as that single string. Keep
  this pattern for any new tool; don't go back to per-field `$fromAI()`
  mappings expecting them to arrive intact.
- **A workflow's `active: true` in its JSON is not sufficient** — see the
  `publish:workflow` note in Step 6.
- **This host needs real memory headroom.** n8n + Postgres + sentinel-agent
  + a spawned `claude` CLI process per generation call add up; on a
  memory-constrained host, concurrent chat sessions can produce a genuine
  out-of-memory crash (n8n reports `Node crashed, possible out-of-memory
  issue`) that has nothing to do with the workflow. Watch `free -h` /
  `docker stats` under load before assuming a bug.

### Claude CLI trade-offs (`LLM_PROVIDER=claude_cli`)

See [app/llm/claude_cli.py](services/sentinel-agent/app/llm/claude_cli.py) for
the full reasoning:
- The model loses its own live `run_kql`/`run_python` self-check tools during
  generation — file reads still work via the CLI's native Read/Grep. The
  actual safety gate (a fresh lint + KQL check) still runs on every draft
  afterward regardless of provider, so this is a quality convenience lost,
  not a validation gap.
- Subscription rate limits are a rolling 5-hour window, not per-token billing
  — a large workbook at a high `PANEL_CONCURRENCY` can hit them sooner than
  the API would. A `529 Overloaded`/`503` from the orchestrator's own chat
  model under heavy concurrent testing is usually this, or Anthropic's API
  being transiently overloaded — wait and retry rather than assuming a bug.
- A personal subscription token driving a shared, multi-analyst sidecar is a
  different usage posture than interactive per-developer use. Treat this as a
  local/dev or cost-saving opt-in, not a silent default swap.

> **If you edit anything under `services/sentinel-agent/app/`, you must
> `docker compose build sentinel-agent` before recreating the container.**
> The Dockerfile does `COPY app ./app` at *build* time; `--force-recreate`
> alone reuses the existing image and silently keeps running the old code.
> `.env`-only changes (model name, provider, etc.) don't need a rebuild —
> those are read fresh at container start.

---

## Validation gates

Nothing reaches Azure unless all of these pass.

| Gate | What it catches |
|---|---|
| **Live KQL** | Syntax errors, and whether the `*_CL` table and every column actually exist. The highest-value check — nothing else here can tell you this. |
| **Structural lint** | The skills' own rules, mechanically: `column_ifexists` on every initial extend, `union isfuzzy` + `dummy_table`, borders and refresh buttons on every panel, no hardcoded subscription ids, real MITRE tactics/techniques on analytic rules, the TDD's anchor headings present. |
| **GET-before-PUT** | Decided inside the deploy step, immediately before the write: does the resource already exist (update) or not (create). |
| **TDD-first gate** | Prompt-level: the orchestrator won't call any other `generate_*` tool until an in-scope TDD draft exists and the analyst has approved it. |
| **Confirmation** | An explicit yes in chat before `deploy_draft`. There is no review step after this. |

Panel-level query validation is implemented but off by default — set
`VALIDATE_PANEL_QUERIES=true` to run every generated panel query before assembly,
at one Log Analytics round trip per panel.

## Failure handling

Fail closed, and never discard completed work.

- **Partial success is still `validated`.** A workbook run producing 96 of 100
  panels keeps the 96, names the 4 misses in `failures`, and is deployable like
  any other validated draft — the analyst decides whether to deploy it as-is
  or regenerate.
- **`needs_input` is a pause, not a failure** — the skills' "stop and ask rather
  than guessing" rule firing as designed.
- **`PUT` is idempotent** by resource name, which is what makes retry-on-timeout
  safe. A property of direct PUT that an ARM deployment wrapper would not give you.
- **There is no automated rollback.** A bad deploy is corrected by fixing the
  draft and deploying again, not by an undo step.

```
generating ──> validated ──> deployed
     ├──────> needs_input
     └──────> failed
```

---

## Testing

```bash
# No model or Azure required: lint calibration against the shipped reference
# artifacts, query composition, byte-determinism of workbook assembly, the
# TDD structure check, and output-path sanitization.
./.venv/bin/python -m pytest tests/ -v

# End-to-end against the real stack — a real model, real tool loop, real
# draft store. Reports the actual generated parser status, or a clear
# diagnosis if the backend itself failed (vs. a bad generation).
./scripts/smoke-test.sh
```

The sharpest available quality signal for whichever model you run: regenerate
`corelight_conn.yaml` via the chat or `/generate/parser`, then diff the
`artifact` field of the resulting draft against the original file. Both
`data/corelight_conn.yaml` and `data/corelight_intel.yaml` are hand-built
parsers whose correct output is already known — the lint suite is calibrated
against them passing clean.

## Layout

Making a change? See [CONTRIBUTING.md](CONTRIBUTING.md) first — editing a file here
usually needs a specific rebuild/recreate/re-import step to actually take effect.

```
skills/                          the skills — source of truth, mounted read-only
  generate-sentinel-tdd/SKILL.md
  generate-sentinel-parser/SKILL.md
  generate-sentinel-analytic-rule/SKILL.md
  generate-sentinel-workbook/SKILL.md
data/                            reference parsers, example dashboard, sample data
db/init.sql                      drafts + deployments schema (fresh volumes)
db/migrations/                   schema changes for volumes created before a given module shipped
docker-compose.yml
docs/azure-setup.md              service principals, roles, verification
n8n/workflows/
  orchestrator.json               the chat agent + all tool node definitions
  deploy-component.json           GET-check, PUT to Azure, record the deployment
  sentinel-tool-*.json            one tiny sub-workflow per tool — see Architecture
output/                           generated artifacts land here, laid out like a solution repo
scripts/smoke-test.sh            real end-to-end check against the running stack
tests/test_pipeline.py           unit tests, no model or Azure required
services/sentinel-agent/
  app/llm/                       AgentRuntime + Anthropic and Claude CLI implementations
  app/tools/                     read_reference, run_python, run_kql
  app/generators/                one module per artifact kind (parser, workbook, analytic_rule, tdd) + _shared.py
  app/workbook/panel_templates.py  deterministic assembly
  app/lint/rules.py              the skills' rules, enforced
  app/output.py                  writes generated artifacts to the shared output folder
  app/store/                     draft and deployment persistence
```

## Security notes

- The sidecar is **not published to the host** — only n8n reaches it, over the
  compose network.
- `run_python` executes model-generated code. It runs unprivileged, in a
  per-call temp directory, under a wall-clock timeout and RLIMIT caps, with only
  read-only mounts and **no Azure write credentials**. The container is the
  boundary; do not widen the mounts or add write credentials to it.
- The deploy service principal lives in n8n's encrypted credential store. The
  sidecar gets a separate **read-only** principal, because it is the component
  running model-authored KQL.
- Postgres is bound to `127.0.0.1` only. Remove that port mapping entirely on a
  shared host.
