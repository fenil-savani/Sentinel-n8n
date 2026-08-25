---
name: generate-sentinel-tdd
description: Generate a customer-ready Technical Design Document (TDD) for a new app/integration
  (Microsoft Sentinel solutions and similar), following the team's house format — Version Control,
  Overview, Compatibility Matrix, Prerequisites, System Architecture (with .drawio diagrams),
  Integration use cases, Technical Implementation Details (Data Connector, Parsers, Analytic Rules,
  Workbooks, Playbooks), MS Sentinel Limitation, References. Authors the markdown at the right depth,
  generates draw.io architecture diagrams, and converts straight to a .docx ready to deliver. Use when
  the user wants to write, draft, or scaffold a TDD / technical design document for any integration.
---

# tdd-generator

Produce a **customer-ready Technical Design Document** for a new integration, then render it to `.docx`
with embedded architecture diagrams. The output matches the team's established TDD house style (learned
from Cyjax, Censys, Vectra, Google SecOps, and GTI TDDs).

The goal depth is **"required minimum detail"** — concrete enough that an engineer can implement from it
and a customer can review it, but not a line-by-line spec. See `reference/tdd-format.md` for the exact
per-section depth contract; follow it closely.

## Workflow

### 1. Gather inputs (ask only for what's missing)
Collect the essentials before writing. If the user already gave them, don't re-ask. Minimum set:
- **Vendor / product name** and a one-line description of what it does.
- **Integration purpose** — what data flows into Sentinel and why (e.g. "ingest GTI alerts and raise incidents").
- **Components in scope** — which of: Data Connector, Parser(s), Analytic Rule(s), Workbook(s), Playbook(s).
  (Not every TDD has all five — only document what exists.)
- **Ingestion mechanism** — Azure Function (Python) timer-pull, Codeless Connector (CCF), or other.
- **Key API details** if known — base URL, auth type (API key / OAuth2 / service account), main endpoint(s).
  If unknown, leave clearly-marked `<TBD>` placeholders rather than inventing specifics.

If the user points you at source material (an API guide, a connector folder, a Jira epic), read it first
and pull the real endpoint/auth/field details from there instead of asking.

### 2. Author the markdown
Write `<Vendor>_<Product>_TDD.md` following `templates/tdd-skeleton.md` and the depth rules in
`reference/tdd-format.md`. Key rules:
- Professional, third-person, present tense. Customer-deliverable tone — no "we will probably".
- Each component appears **twice**: a short *Integration use cases* entry (1 paragraph + **Acceptance Criteria**)
  and a detailed *Technical Implementation Details* entry.
- Use **tables** for: Version Control, endpoint property/request/response, query parameters, response codes,
  User Inputs (ARM params), parser field mapping, playbook endpoints, enrichment field maps.
- Never fabricate API contracts, schemas, or limits. Use `<TBD>` (or `<Internal Discussion>`) for unknowns —
  the reference docs do this routinely.
- Keep the two architecture headings exactly named so diagrams auto-place:
  **`Overall System Architecture`** and **`Data Connector Architecture`**.

### 3. Generate the architecture diagrams (draw.io)
Copy the two templates and customize the labels for this integration:
- `templates/overall-architecture.drawio` → `<Vendor>_Overall_Architecture.drawio`
- `templates/data-connector-architecture.drawio` → `<Vendor>_DataConnector_Architecture.drawio`

Replace the `{{VENDOR}}`, `{{PRODUCT}}`, `{{TABLE}}` and similar placeholders with real names. Keep the
mxGraphModel XML valid (don't break tags). Add/remove boxes to match the real data flow. If a component
(e.g. Playbook) isn't in scope, drop its box.

### 4. Convert to .docx with diagrams embedded
Run the bundled converter. It renders each `.drawio` to PNG (via the diagrams.net export server) and
auto-places it right after the matching heading.

```powershell
$py = "C:\Users\devendra.chavda\Desktop\Sentinel-work\Automation\sentinel-pr-review\.venv\Scripts\python.exe"
$skill = "C:\Users\devendra.chavda\.claude\skills\tdd-generator"
& $py "$skill\scripts\md_to_docx.py" "<Vendor>_<Product>_TDD.md" `
  -o "<Vendor>_<Product>_TDD.docx" `
  --title "<Vendor> Microsoft Sentinel Integration - Technical Design Document" `
  --toc `
  --diagram "<Vendor>_Overall_Architecture.drawio::Overall System Architecture" `
  --diagram "<Vendor>_DataConnector_Architecture.drawio::Data Connector Architecture"
```

Formatting is fixed to the team house style automatically: **Arial** everywhere, **black headings**
sized per level (Title 26 / H1 20 / H2 16 / H3 14 (#434343) / H4 12 (#666666) pt), tables with a
gray `#BFBFBF` header row and thin black borders. Flags:
- `--toc` inserts a Word Table of Contents field (open in Word → right-click → *Update Field* to fill it).
- `--logo <path>` places a logo image centered at the top, above the title.

The converter needs `python-docx` + `requests`. The Sentinel project `.venv` above already has both.
Any Python with those two packages works (`pip install python-docx requests`).

> **Privacy note:** `--diagram` uploads the diagram XML to `convert.diagrams.net` (third-party) to render
> the PNG. For sensitive architecture, either omit `--diagram` (text-only docx) or export the PNGs manually
> from app.diagrams.net and embed those instead. Mention this to the user when diagrams contain sensitive detail.

### 5. Verify and hand off
Confirm the docx: report paragraph / table / image counts (the script prints the size). Tell the user the
output path, which sections are `<TBD>`, and what still needs their input.

## Files
- `reference/tdd-format.md` — section-by-section structure + depth contract + house phrasing. **Read this before writing.**
- `templates/tdd-skeleton.md` — fill-in markdown skeleton in the exact section order.
- `templates/overall-architecture.drawio` — Overall System Architecture diagram template.
- `templates/data-connector-architecture.drawio` — Data Connector internals diagram template.
- `scripts/md_to_docx.py` — markdown→docx converter with .drawio rendering + heading-anchored image placement.
