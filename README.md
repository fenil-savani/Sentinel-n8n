# Sentinel Component Builder

Chat with an agent to generate Microsoft Sentinel **parsers** and **workbooks**,
and deploy them straight into your workspace — with a snapshot, a diff, and an
explicit confirmation in front of every write.

The generation rules live in two files at the repo root, which are the single
source of truth for both humans and the agent:

- [skills/generate-sentinel-parser/SKILL.md](skills/generate-sentinel-parser/SKILL.md)
- [skills/generate-sentinel-workbook/SKILL.md](skills/generate-sentinel-workbook/SKILL.md)

Edit either one and the next generation picks it up — no rebuild, no second copy.

---

## Architecture

```
Analyst ── n8n chat widget
   │
   ▼
Chat Trigger ──> Orchestrator agent  (Claude)
   │
   ├─ generate_parser ─┐
   ├─ generate_workbook├──HTTP──> sentinel-agent   runs the two .md skills with
   ├─ validate_kql     │          (sidecar)        real tools: read files, run
   ├─ lint_draft       │                          Python, execute KQL
   ├─ describe_draft ──┘
   │
   └─ deploy_draft ──> n8n sub-workflow   GET~~-check, then direct PUT to Azure

Postgres: n8n state │ drafts + deployments
```

Three design decisions carry most of the weight:

**Artifacts never enter the model's context.** A workbook can be 180 KB. Every
generator writes the artifact to Postgres and returns a `draft_id` plus a small
summary; the orchestrator reasons over the summary. This is also why an approval
turn hours after generation still works — nothing depends on chat memory.

**Workbooks are assembled by code, not by the model.** The model plans panels,
then writes one ~1 KB query object per panel. Every structural field — envelope,
groups, parameters, borders, refresh buttons, grid settings — comes from
templates in [`panel_templates.py`](services/sentinel-agent/app/workbook/panel_templates.py),
extracted from the shipped `CorelightDataExplorer.yaml`. The model never has to
remember `"resourceType": "microsoft.operationalinsights/workspaces"`, and
cannot forget it.

**The model never authors an Azure write.** Its authority stops at
`deploy_draft(draft_id)`. Deterministic code in the deploy workflow builds the
Azure request from the stored artifact and PUTs it — the model only ever names
which draft to deploy.

---

## Status

**Built and verified:** generation pipeline (parser + workbook), structural lint
calibrated against the shipped reference artifacts, draft/deployment store, both
n8n workflows, KQL validation client. 17 unit tests passing against a stubbed
model (`tests/test_pipeline.py`).

**Not yet verified:** a real end-to-end run against a live model, and any real
Azure deployment. Nothing has actually been deployed to a Sentinel workspace yet.

### Next steps, in order

1. Work through **## Setup** below, end to end.
2. **Azure setup** — [docs/azure-setup.md](docs/azure-setup.md). Run §5c's `curl`
   checks against a scratch resource group *before* trusting the deploy path —
   it catches a wrong `api-version` or the workbook GUID/`category` requirement
   for free.
3. **Deploy** to a non-production workspace: deploy → confirm it's callable in
   Sentinel Logs → redeploy with a change → confirm it updates the same
   resource rather than creating a duplicate.
4. **Then try a small workbook** (3-5 panels) before anything workbook-shaped
   and larger.

Queue mode (Redis), backups, rollback, and structured tracing are deliberately
deferred until the above is solid — no need to think about them yet.

---

## Setup

### 1. Prerequisites

- Docker + Docker Compose.
- One LLM path for `sentinel-agent`'s own generation calls, decided now (you
  can switch later by editing `.env` and recreating the container):
  - **Anthropic API key** (default, `LLM_PROVIDER=anthropic`) — from
    console.anthropic.com, billed per token.
  - **Claude CLI / Claude Code subscription** (`LLM_PROVIDER=claude_cli`, no
    API key) — needs a machine already logged in via `claude` (check with
    `claude auth status`). See the trade-offs below before using this for a
    shared deployment.

This is independent of the **n8n orchestrator's own chat model**, which is a
separate Anthropic credential created inside n8n in Step 4 regardless of which
`LLM_PROVIDER` you pick here.

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
> a separate volume and is unaffected either way.
- Either `ANTHROPIC_API_KEY` (leave `LLM_PROVIDER=anthropic`), or set
  `LLM_PROVIDER=claude_cli` and run `claude setup-token` on a machine already
  logged in to Claude Code, pasting the result into `CLAUDE_CODE_OAUTH_TOKEN`.
- `LLM_MODEL_PARSER` / `LLM_MODEL_WORKBOOK_MANIFEST` / `LLM_MODEL_PANEL` —
  sensible defaults are already set (opus for the two reasoning-heavy flows,
  sonnet for the high-volume per-panel loop); override only if you want a
  different model for one specific flow.
- Azure variables can be left blank for now — the stack runs without them,
  with KQL validation degrading to a documented skip. Fill them in before
  Step 6 below.

### 3. Start the stack

```bash
docker compose up -d
docker compose logs -f sentinel-agent
```

Confirm a line like `ready: provider=anthropic parser_model=claude-opus-5
manifest_model=claude-opus-5 panel_model=claude-sonnet-5 azure=False`. If you
see `Error in sub-node ...` from `docker compose logs -f n8n` right now, that's
expected — no n8n credentials exist yet. Continue to Step 4.

### 4. Create n8n credentials

Open **http://localhost:5678** (or your configured `N8N_HOST`/`N8N_PORT`) and
create the owner account. Then, under **Credentials → Add credential**, create
exactly these three. **Importing a workflow file does not create its
credentials** — a node's credential reference is just an id baked into the
JSON until you link a real credential to it:

| Credential | Type | Fill in | Link to node(s) |
|---|---|---|---|
| Sentinel Postgres | Postgres | host `postgres`, port `5432`, database/user/password from `.env` | **Postgres Chat Memory** in *Sentinel — Orchestrator* |
| Anthropic account | Anthropic | an Anthropic API key (separate from `sentinel-agent`'s own `LLM_PROVIDER` — the orchestrator's chat model always needs its own key) | **Anthropic Chat Model** in *Sentinel — Orchestrator* |
| Azure Management (client credentials) | OAuth2 (client credentials) | see [docs/azure-setup.md](docs/azure-setup.md) §4 | **GET Current Resource** and **PUT to Azure** in *Sentinel — Deploy Component* |

To link: open the workflow → click the node → pick the credential from its
dropdown → **save the workflow** (the node panel and the workflow have
separate saves; closing the node panel alone doesn't persist it).

### 5. Import and activate the workflows

```bash
docker compose exec n8n n8n import:workflow --separate --input=/workflows
```

Open **Sentinel — Orchestrator**, re-check that the three credentials from
Step 4 are still linked (a fresh import can reset a node's credential
selection back to the unlinked id), then **activate** it.

> **If you edit a workflow JSON file** (e.g. `n8n/workflows/orchestrator.json`'s
> system prompt), editing it on disk has no effect on the running instance —
> re-run the `import:workflow` command above to push the change in, then
> re-check credentials are still linked and the workflow is still active,
> same as any fresh import.

### 6. Verify

```bash
docker compose exec sentinel-agent python -c \
  "import urllib.request, json; print(json.load(urllib.request.urlopen('http://localhost:8000/healthz')))"
```

Confirm `llm_provider`, `parser_model`, `workbook_manifest_model`, and
`panel_model` match what you set in `.env`. `sentinel-agent` isn't published to
the host (see **Security notes**), so `/healthz` is only reachable from inside
the compose network, as above.

### 7. First real generation

Open the Chat panel on **Sentinel — Orchestrator**, ask for the Corelight conn
parser, and diff the result against `data/corelight_conn.yaml` — that diff is
the real quality signal, not anything in this doc.

### 8. Azure + deploy (when ready)

Follow [docs/azure-setup.md](docs/azure-setup.md) end to end, including §5c's
`curl` checks against a scratch resource group *before* trusting the deploy
path. Then: deploy → confirm it's callable in Sentinel Logs → redeploy with a
change → confirm it updates the same resource rather than duplicating it.

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
  the API would.
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
| **Structural lint** | The skills' own rules, mechanically: `column_ifexists` on every initial extend, `union isfuzzy` + `dummy_table`, borders and refresh buttons on every panel, no hardcoded subscription ids. |
| **GET-before-PUT** | Decided inside the deploy step, immediately before the write: does the resource already exist (update) or not (create). |
| **Confirmation** | An explicit yes in chat. There is no review step after this. |

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
# 17 tests, no model or Azure required: lint calibration against the shipped
# reference artifacts, query composition, byte-determinism of workbook
# assembly, every Step 6/8 rule.
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
  generate-sentinel-parser/SKILL.md
  generate-sentinel-workbook/SKILL.md
data/                            reference parsers, example dashboard, sample data
db/init.sql                      drafts + deployments schema
docker-compose.yml
docs/azure-setup.md              service principals, roles, verification
n8n/workflows/                   orchestrator, deploy
scripts/smoke-test.sh            real end-to-end check against the running stack
tests/test_pipeline.py           17 unit tests, no model or Azure required
services/sentinel-agent/
  app/llm/                       AgentRuntime + Anthropic and Claude CLI implementations
  app/tools/                     read_reference, run_python, run_kql
  app/generators/                parser and workbook generation
  app/workbook/panel_templates.py  deterministic assembly
  app/lint/rules.py              the skills' rules, enforced
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
