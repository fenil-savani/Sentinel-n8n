# Contributing / Dev Workflow

This stack is three services glued together by Docker Compose, a Postgres database, and
files mounted into containers. **Editing a file on disk does not automatically mean the
running system uses it** — different kinds of changes need different follow-up actions.
This doc is the lookup table for "I changed X, now what do I run."

If you only remember one thing: **`docker compose up -d` recreates containers from the
existing image; it does not rebuild the image.** If you edited Python code or the
Dockerfile, you need `docker compose build` first, or you'll keep running old code with
no error to tell you so.

---

## Quick reference

| You changed... | You must run... | Why |
|---|---|---|
| `services/sentinel-agent/app/**/*.py` | `docker compose build sentinel-agent`<br>`docker compose up -d sentinel-agent` | The Dockerfile does `COPY app ./app` at **build** time. A plain recreate reuses the old image and silently keeps running old code. |
| `services/sentinel-agent/Dockerfile` or `requirements.txt` | same as above | Same reason — these only take effect in a fresh image. |
| `docker-compose.yml` (env vars, volume mounts, ports) | `docker compose up -d` | Recreates containers with the new compose config. No rebuild needed unless you *also* touched the Dockerfile/app. |
| `.env` values | `docker compose up -d <service>` | Env vars are read at container start, not baked into the image. Recreate is enough — no rebuild. **Exception:** `N8N_ENCRYPTION_KEY` — see the callout below, this one is dangerous to just "change and recreate." |
| `skills/generate-sentinel-parser/SKILL.md`<br>`skills/generate-sentinel-workbook/SKILL.md` | **Nothing.** | Mounted read-only into the container; `app/prompts.py` re-reads the file when its mtime changes. Takes effect on the *next* generation call, no restart. |
| `n8n/workflows/*.json` (e.g. the orchestrator's system prompt) | `docker compose exec n8n n8n import:workflow --separate --input=/workflows`<br>then re-link credentials and republish (see below) | Editing the file on disk has zero effect on the running n8n instance until re-imported. **Re-importing an existing workflow resets both its published state and every node's credential link back to the placeholder id baked into the JSON** — this is not optional cleanup, it happens every time. Budget for redoing both after any workflow JSON edit. |
| `db/init.sql` | Nothing, on an existing stack — it only runs once, on first init of an empty Postgres data directory. To apply schema changes: either write a migration and apply it manually, or accept wiping `postgres_data` (see Full Reset below, and note that also destroys `sentinel-agent`'s drafts/deployments). |

---

## The credential-relink dance (n8n workflow edits)

This is the single most disruptive part of the dev loop, so it's worth calling out on
its own. Every time you edit a workflow JSON file and re-import it:

1. Both workflows' published state resets (see the publish/draft section below).
2. Every node's credential reference resets to the raw placeholder id from the JSON
   (e.g. `N8N_POSTGRES`, `SENTINEL_CLI_OPENAI`) — not a real credential.

After **any** re-import, redo this in the n8n UI:

| Credential | Type | Link to node(s) |
|---|---|---|
| Sentinel Postgres | Postgres | **Postgres Chat Memory** in *Sentinel — Orchestrator* |
| Sentinel CLI (OpenAI-compatible) — Base URL `http://sentinel-agent:8000/v1` | OpenAI | the chat-model node in *Sentinel — Orchestrator* (still labeled "Anthropic Chat Model" in the canvas even though it's an OpenAI-type node — see `app/llm/openai_compat.py`) |
| Azure Management (client credentials) | OAuth2 | **GET Current Resource** + **PUT to Azure** in *Sentinel — Deploy Component* |

Then republish both workflows — see the next section, **use the CLI command, not the UI
button**.

For a **small text tweak** to one node (e.g. adjusting a system prompt), editing it
directly in the n8n UI instead of the JSON file avoids this entire dance — only reach
for file-edit + re-import when the change is structural (new nodes, new connections)
and needs to be the source of truth on disk.

---

## Draft vs. published workflows — the stale-snapshot gotcha

This n8n version (2.35.5+) has a separate **draft vs. published** concept, distinct from
the classic active/inactive toggle: editing a workflow (including relinking a credential)
updates the *draft* (`workflow_entity`), but the chat trigger's live webhook runs whatever
was captured in `workflow_published_version` at the moment you last published. **If you
publish, then edit something afterward (e.g. fix a credential), the live workflow keeps
using the old, pre-fix snapshot** until you publish again — even though the editor and a
direct DB query against `workflow_entity` both show the fix is there. This produces a
confusing symptom: the exact same error you already fixed comes back, because the running
instance was never looking at your fix in the first place.

Confirm which case you're in before assuming a fix didn't work:

```bash
# 0 rows here (for a workflow that "should" be published) means it's never been
# published at all in this instance's lifetime, or was fully unpublished.
docker compose exec postgres psql -U sentinel -d n8n -c \
  "SELECT \"workflowId\", \"createdAt\" FROM workflow_published_version;"
```

The UI's Publish button can leave this in a state that's hard to reason about. **Prefer
the CLI**, which publishes directly from the workflow's current draft state in one
unambiguous step:

```bash
docker compose exec n8n n8n unpublish:workflow --all
docker compose exec n8n n8n import:workflow --separate --input=/workflows
# ... relink credentials in the UI, per the table above, then save both workflows ...
docker compose exec n8n n8n publish:workflow --id=sentinel-orchestrator
docker compose exec n8n n8n publish:workflow --id=sentinel-deploy
```

There is no `delete:workflow` CLI command in this n8n version — `import:workflow` upserts
by the workflow's `id`, so re-importing is the correct way to replace a workflow's
contents, not delete-then-recreate.

---

## `N8N_ENCRYPTION_KEY` — generate once, never regenerate

n8n writes this key into `/home/node/.n8n/config` inside the `n8n_data` volume the
**first time it ever starts**, and encrypts every stored credential with it. If `.env`'s
value ever stops matching what's in that volume, n8n refuses to start at all:

```
Error: Mismatching encryption keys. The encryption key in the settings file
/home/node/.n8n/config does not match the N8N_ENCRYPTION_KEY env var.
```

- Only run `openssl rand -hex 32` for this value on a genuinely first-ever setup.
- If you hit the mismatch: restore the original key in `.env` if you still have it
  (zero data loss), or accept wiping n8n's data and recreate the volume (see below).

---

## Full reset (nuclear option)

Wipes n8n's workflows/credentials/executions **and** `sentinel-agent`'s stored
drafts/deployments (both live in the same Postgres instance, different databases).
Nothing outside this compose project is touched.

```bash
docker compose down -v
docker compose build sentinel-agent   # picks up any pending code/Dockerfile changes
docker compose up -d
```

**Gotcha we hit in practice:** `docker compose stop` + `docker volume rm` can silently
no-op — Docker considers a volume "in use" as long as *any* container references it,
even a stopped one, and n8n's `restart: unless-stopped` policy revives the container
before removal finishes. If a volume removal step ever reports success but the old data
persists, force it explicitly:

```bash
docker rm -f sentinel-n8n-n8n-1        # -f removes regardless of running/stopped state
docker volume rm sentinel-n8n_n8n_data --force
docker compose up -d n8n
```

After a full reset, you're starting from an empty n8n — redo README.md's Setup Steps
4–7 (credentials, import, activate, verify, first generation).

---

## Verifying a change actually landed

Don't trust "the command didn't error" — verify directly:

```bash
# sentinel-agent: confirm the running code, not just the file on disk
docker logs sentinel-n8n-sentinel-agent-1 --tail 5 | grep "ready:"
docker compose exec sentinel-agent python -c \
  "import urllib.request, json; print(json.load(urllib.request.urlopen('http://localhost:8000/healthz')))"

# n8n: confirm a workflow JSON edit actually reached the database, not just the file
docker compose exec postgres psql -U sentinel -d n8n -c \
  "SELECT id, active FROM workflow_entity;"

# n8n: confirm it's actually *published* (see the draft-vs-published section above) —
# active=true alone does not mean the live webhook reflects your latest edit
docker compose exec postgres psql -U sentinel -d n8n -c \
  "SELECT \"workflowId\", \"createdAt\" FROM workflow_published_version;"

# n8n: pull the real error out of a failed execution — the chat UI's "Failed to
# receive response" and n8n's own "Error in workflow" are both too generic to debug from
docker compose exec postgres psql -U sentinel -d n8n -t -c \
  "SELECT ed.data FROM execution_data ed JOIN execution_entity e ON e.id = ed.\"executionId\" \
   WHERE e.\"workflowId\"='sentinel-orchestrator' ORDER BY e.\"startedAt\" DESC LIMIT 1;"
# The result is n8n's flattened execution-data format (a JSON array where later
# elements are referenced by index from earlier ones) — not pretty, but the real
# error message and stack trace are in there as plain readable strings.
```

If you edit Python under `app/` and `git status`/`git diff` shows your change is
present on disk but the container's behavior doesn't reflect it, the image almost
certainly wasn't rebuilt — check `docker images sentinel-n8n-sentinel-agent` and compare
its `CreatedAt` against your file's mtime before debugging anything else.
