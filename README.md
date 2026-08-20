# Sentinel Component Builder

Chat with an agent to generate Microsoft Sentinel **parsers** and **workbooks**,
and deploy them straight into your workspace — with a snapshot, a diff, and an
explicit confirmation in front of every write.

The generation rules live in two files at the repo root, which are the single
source of truth for both humans and the agent:

- [skills/generate-sentinel-parser.md](skills/generate-sentinel-parser.md)
- [skills/generate-sentinel-workbook.md](skills/generate-sentinel-workbook.md)

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
   └─ deploy_draft ──> n8n sub-workflow   GET-check, then direct PUT to Azure

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

1. **Unblock generation** — add a real `ANTHROPIC_API_KEY` to `.env`, recreate
   `sentinel-agent`, and create the n8n **Anthropic account** credential.
2. **Azure setup** — [docs/azure-setup.md](docs/azure-setup.md). Run §5c's `curl`
   checks against a scratch resource group *before* trusting the deploy path —
   it catches a wrong `api-version` or the workbook GUID/`category` requirement
   for free.
3. **Create the remaining n8n credentials** — Sentinel Postgres (chat memory),
   Azure Management OAuth2.
4. **First real generation** — chat with the orchestrator, ask for the Corelight
   conn parser, diff the result against `data/corelight_conn.yaml`. That diff is
   the real quality signal, not anything in this doc.
5. **Deploy** to a non-production workspace: deploy → confirm it's callable in
   Sentinel Logs → redeploy with a change → confirm it updates the same
   resource rather than creating a duplicate.
6. **Then try a small workbook** (3-5 panels) before anything workbook-shaped
   and larger.

Queue mode (Redis), backups, rollback, and structured tracing are deliberately
deferred until 1-5 above are solid — no need to think about them yet.

---

## Quickstart

Default provider is **Anthropic** (`claude-opus-5` / `claude-sonnet-5`).

```bash
cp .env.example .env          # then edit; generate secrets with `openssl rand -hex 32`
# set ANTHROPIC_API_KEY in .env
docker compose up -d
```

Open **http://localhost:5678**, create the owner account, then:

1. Create the credentials listed in [docs/azure-setup.md](docs/azure-setup.md) §4, plus
   an **Anthropic account** credential (Credentials → Add → Anthropic) for the
   orchestrator's chat model node.
2. Import the workflows if they are not already there:
   ```bash
   docker compose exec n8n n8n import:workflow --separate --input=/workflows
   ```
3. Open **Sentinel — Orchestrator**, activate it, and use the Chat panel.

For Azure — service principals, roles, and the verification steps to run *before*
trusting the deploy path — follow [docs/azure-setup.md](docs/azure-setup.md).

The stack runs without Azure: KQL validation degrades to a documented skip, so
you can develop generation offline. Without `ANTHROPIC_API_KEY` set, the sidecar
still starts and every endpoint except `/generate/*` works normally — a
generation call returns a clean `503` naming exactly what's missing, rather than
crash-looping the container.

> **If you edit anything under `services/sentinel-agent/app/`, you must
> `docker compose build sentinel-agent` before recreating the container.**
> The Dockerfile does `COPY app ./app` at *build* time; `--force-recreate`
> alone reuses the existing image and silently keeps running the old code.
> `.env`-only changes (model name, etc.) don't need a rebuild — those are read
> fresh at container start.

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

```
skills/                          the skills — source of truth, mounted read-only
  generate-sentinel-parser.md
  generate-sentinel-workbook.md
data/                            reference parsers, example dashboard, sample data
db/init.sql                      drafts + deployments schema
docker-compose.yml
docs/azure-setup.md              service principals, roles, verification
n8n/workflows/                   orchestrator, deploy
scripts/smoke-test.sh            real end-to-end check against the running stack
tests/test_pipeline.py           17 unit tests, no model or Azure required
services/sentinel-agent/
  app/llm/                       AgentRuntime + Anthropic implementation
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
