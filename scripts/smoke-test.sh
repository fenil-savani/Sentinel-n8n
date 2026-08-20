#!/usr/bin/env bash
# End-to-end smoke test against the running stack.
#
# Exercises the real path — real model, real tool loop, real draft store — and
# reports what actually came back rather than just whether HTTP 200 happened.
# Azure is optional: without it, KQL validation is skipped and reported as such.
#
#   ./scripts/smoke-test.sh
#
# Uses Node's core `http` module inside the n8n container — not wget, not
# `fetch`:
#   - BusyBox wget (the only HTTP client in that image) silently discards the
#     response body on any non-2xx status — a 503 with a useful error message
#     in the JSON body was invisible until this switched off wget.
#   - Node's global `fetch` (undici) has its own default 300s headersTimeout,
#     independent of any timeout configured on the server side. It aborted a
#     request the sidecar was still legitimately working on. Plain `http`
#     with an explicit long timeout has no such hidden ceiling.
set -uo pipefail
cd "$(dirname "$0")/.."

hr() { printf '\n\033[1m%s\033[0m\n' "$*"; }

# $1: method, $2: path, $3: body (or ""), $4: timeout ms
http_call() {
  docker compose exec -T n8n node -e '
const http = require("http");
// `node -e "..." a b c` gives process.argv = [nodeBinary, a, b, c] — unlike
// running a file, there is no script-path slot to skip.
const [, method, path, body, timeoutMs] = process.argv;
const start = Date.now();
const req = http.request(
  { hostname: "sentinel-agent", port: 8000, path, method,
    headers: body ? { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(body) } : {},
    timeout: Number(timeoutMs) },
  (res) => {
    let data = "";
    res.on("data", (c) => (data += c));
    res.on("end", () => {
      const elapsed = Math.round((Date.now() - start) / 1000);
      console.error(`HTTP ${res.statusCode} (${elapsed}s)`);
      console.log(data || "{}");
    });
  }
);
req.on("timeout", () => { req.destroy(new Error(`client timeout after ${timeoutMs}ms`)); });
req.on("error", (e) => {
  const elapsed = Math.round((Date.now() - start) / 1000);
  console.error(`REQUEST FAILED after ${elapsed}s: ${e.message}`);
  console.log("{}");
});
if (body) req.write(body);
req.end();
' "$1" "$2" "$3" "$4"
}

hr "1. Sidecar health"
RESP=$(http_call GET /healthz "" 10000)
echo "$RESP" | python3 -m json.tool 2>/dev/null || echo "$RESP"
echo "$RESP" | python3 -c 'import sys,json; sys.exit(0 if json.load(sys.stdin).get("ok") else 1)' 2>/dev/null \
  || { echo "sidecar unreachable"; exit 1; }

hr "2. Generate a parser"
BODY='{"product":"Corelight","logtype":"conn","table":"Corelight_v2_conn_CL","reference_parser":"corelight_conn.yaml","session_id":"smoke"}'
RESP=$(http_call POST /generate/parser "$BODY" 300000)
echo "$RESP" | python3 -m json.tool 2>/dev/null || echo "$RESP"

STATUS=$(echo "$RESP" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("status","?"))' 2>/dev/null)
DRAFT=$(echo  "$RESP" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("draft_id",""))' 2>/dev/null)
DETAIL=$(echo "$RESP" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("detail",""))' 2>/dev/null)

hr "3. Verdict"
case "$STATUS" in
  validated)
    echo "PASS — parser generated and passed every gate."
    echo "Compare it against the hand-written original:"
    echo "  docker compose exec n8n node -e '\
const http=require(\"http\");http.get({hostname:\"sentinel-agent\",port:8000,path:\"/drafts/$DRAFT\"},r=>{let d=\"\";r.on(\"data\",c=>d+=c);r.on(\"end\",()=>console.log(JSON.parse(d).artifact));});' \
> /tmp/generated.yaml"
    echo "  diff -u data/corelight_conn.yaml /tmp/generated.yaml"
    ;;
  needs_input)
    echo "PAUSED — the generator asked for more information. That is the skill's"
    echo "'stop and ask rather than guess' rule working, not a failure."
    ;;
  failed)
    echo "FAILED — the draft did not pass validation. The findings above say why;"
    echo "it means the gates held and nothing invalid would reach Azure."
    ;;
  *)
    if [ -n "$DETAIL" ]; then
      echo "SIDECAR ERROR (HTTP 503) — $DETAIL"
      echo "This means the backend itself failed (e.g. the Anthropic API was"
      echo "unreachable or ANTHROPIC_API_KEY is unset), not that generation ran"
      echo "and produced a bad draft."
      echo "Check: docker compose logs sentinel-agent --tail=50"
    else
      echo "UNEXPECTED — no parseable response. Check: docker compose logs sentinel-agent --tail=50"
    fi
    ;;
esac

hr "4. Drafts recorded"
RESP=$(http_call GET /drafts?session_id=smoke "" 10000)
echo "$RESP" | python3 -m json.tool 2>/dev/null || echo "$RESP"
