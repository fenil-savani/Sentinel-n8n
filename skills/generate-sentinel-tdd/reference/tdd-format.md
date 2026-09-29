# TDD Format & Depth Contract

This is the canonical structure and depth for a Technical Design Document, distilled from delivered TDDs
(Cyjax, Censys, Vectra, Google SecOps, GTI). Follow the section order exactly. Document **only the
components that exist** in the integration — drop sections for components not in scope.

Depth target: an engineer can implement from it; a customer can review it. **Not** a high-level summary,
**not** a line-by-line spec. Concrete names, real endpoints, real field maps — but prose stays tight
(2–6 sentences per concept), and detail lives in tables.

---

## Heading levels
- H1 = top-level sections (Overview, System Architecture, Integration use cases, …)
- H2 = component within a section (Data Connector, Parser, Playbook, …)
- H3–H6 = drill-down (specific connector → functions → endpoints → fields)

---

## 1. Title block (top of doc, before first H1)
Two centered lines: the integration name and the version.
```
<Vendor> <Product> Microsoft Sentinel Integration
v1.0.0
```

## 2. Version Control (H1)
A single table. Columns: `#`, `Document Version`, `Date`, `Owner`, `Document Status`, `Comments`.
One row per revision. First row example: `1 | 1.0.0 | <Month Dayth, Year> | Crest Data | Initial Draft | `.

## 3. Contents (H1) — optional
Either a manual bullet list of sections, or omit (Word can generate a TOC). Most recent docs omit it.

## 4. Overview (H1)
Three H2 subsections, ~1 short paragraph (plus a few bullets for Sentinel):
- **Microsoft Sentinel Platform** — standard boilerplate: cloud-native SIEM/SOAR; then 4 capability bullets
  (Collect / Detect / Investigate / Respond). Reuse the canonical wording.
- **`<Vendor>` Platform** — 2–4 sentences on what the vendor product is and the value it provides.
- **`<Vendor>` Microsoft Sentinel Integration** — 2–4 sentences: how the integration is implemented
  (custom data connector / CCF), what data it retrieves, and the outcome in Sentinel.

## 5. Compatibility Matrix (H1)
A few `Key : Value` lines for versions, e.g. `Python Version : 3.12`, `<Vendor> API Version : v1beta`.

## 6. Prerequisites (H1)
Grouped bullet list of what the customer must have. Standard groups:
- **Azure Account subscription** (owner role to register an Entra app; Function App; Storage Account;
  Log Analytics Workspace; Microsoft Sentinel).
- **Workspace** read/write permissions; `Microsoft.Web/sites` permissions.
- **REST API Credentials/Permissions** — the vendor API credential required.
Keep it to the real prerequisites for this integration's mechanism (a CCF connector has different prereqs
than an Azure Function one).

## 7. System Architecture (H1)
Opening line: "`<Integration>` consists of the following components" + a bullet list of the components
(Data Connector, Parsers, Analytic Rule, Workbook, Playbook, Solution Packaging & Documentation).
Then:
- **Overall System Architecture** (H2) — the end-to-end diagram (vendor API → connector → Log Analytics /
  Sentinel → analytic rules → incidents → playbooks). *Diagram is inserted here.*
- **Data Connector Architecture** (H2 or H4) — the connector-internals diagram (timer → auth → fetch →
  normalize → Log Ingestion API → custom table; checkpoint in storage). *Diagram is inserted here.*

> Keep these two heading texts EXACTLY so the converter's `--diagram ...::Heading` auto-placement matches.

## 8. Integration use cases (H1)
Opening line: "This section describes the use cases provided as part of the `<Vendor>` – Microsoft Sentinel
integration. The integration will be distributed as a `<Vendor>` Solution."
Then **one H2 per component**, each = a short paragraph describing the use case + an **Acceptance Criteria**
bullet list. Examples of acceptance criteria seen in practice:
- Parser: "The parser must accurately parse the data without any errors."
- Workbook: "The dashboards should visualize the data accurately. Users can filter per the provided filters."
- Playbook: "The Playbooks must enrich data with required fields as a comment to the incident. The Playbooks
  must execute when requested by the SOC user or set by automation rule."
- Data Connector: 1–2 sentences on what it ingests and how (API-based, timer-driven).
- Analytic Rule: what conditions raise incidents and how attributes map.

## 9. Technical Implementation Details (H1)
Opening line: "This section describes the technical implementation details for the use cases provided as
part of this integration." Then **one H2 per component**, deep:

### Data Connector (H2)
- One H3 per connector (e.g. "`<Vendor>` Alerts Data Connector"). Describe the mechanism in a few sentences,
  then named sub-flows (as labeled paragraphs or H4/H5):
  - **Azure Function** breakdown — if multi-function, name each (e.g. "Vendor → Azure Storage",
    "Azure Storage → Sentinel") and what each does.
  - **Authentication Flow** — step list (exchange key → token → call API).
  - **Alert/Data Fetching and Filtering** — incremental pull strategy (by updated time), user-controlled filters.
  - **Ingestion to Microsoft Sentinel** — normalization + Log Ingestion API into the target table.
  - **Checkpoint Mechanism** — how duplicate ingestion is avoided / continuation across runs.
  - **Reliability and Retry Handling** — retry on transient failures.
- **Endpoint details** — for each API endpoint used, give tables:
  - Property table (`Property | Value`: Endpoint, Method, Content-Type, Base URL, Full URL Example).
  - Request Body (code block / single-cell table).
  - Sample Successful Response (200) and Sample Error Response (4xx) — emit the `<API Response>` tag for each
    (see the tag rule under Playbook); the user pastes real captured responses. Do not fabricate JSON.
  - Response Field table (`Field | Type | Description`).
  - Query Parameters table (`Parameter | Type | Required | Description`).
  - Supported filter fields table (`Field | Description`).
  - Response Codes table (`HTTP Code | Type | Meaning`).
  - Token Lifecycle notes (validity, refresh cadence, never persisted) where OAuth is used.
- **User Inputs** (H4) — ARM template parameters as a table: `Name | Required | Description | Default`
  (FunctionName, Location, WorkspaceName, AppInsightsWorkspaceResourceID, API creds, filters, schedule…).
- **Data Connector Trigger** (H3) — the timer/schedule; usually a screenshot/diagram placeholder.
- **Prerequisites** (H3) — Azure creds needed to deploy (Tenant ID, Client ID, Client Secret, Resource Group,
  Subscription ID).
- **Post deployment steps** (H3) — H4 step lists, e.g. "Steps to Obtain `<Vendor>` API Key",
  "Assign Required Permissions", "Configure OAuth Scope".

### Parser (H2)
One H3 per parser (e.g. `<Vendor>Alerts`, `<Vendor>Host`). 2–4 sentences each: what raw data it processes,
how it normalizes/flattens nested JSON into the target schema, and that the output feeds analytic rules /
workbooks. For ASIM parsers, name the schema. A field-mapping table (`Field Label | Response Field Path |
Data Type`) is strongly preferred when fields are known.

### Analytic Rules (H2)
2–4 sentences: the rule generates incidents from the ingested data; what conditions/thresholds it applies;
how it maps attributes (severity, IDs, entities like IP/host/user) to the incident schema and entity mappings
for the investigation graph.

### Workbook (H2) — if in scope
One H3 per dashboard. Under each, **Panels** (table: `Panel Name | Type of Visualization | Description`) and
**Filters** (the parameters users can filter by). Use `<TBD>` for unfinished dashboards.

### Playbook (H2) — if in scope
There are two common playbook shapes. Pick the one that matches the integration (a TDD can have both):

**(a) Enrichment playbooks** (e.g. Censys) — pull vendor data and write it back as incident comments.
- A **Playbook Prerequisites** note (API key in Key Vault; playbook granted Key Vault access; incident entities
  IP/Domain/SHA mapped).
- One H3 per playbook. **Workflow** as an ordered narrative: trigger (automation rule / manual) → fetch secret
  from Key Vault → read incident entities → call vendor API(s) → add enrichment as incident comments
  (respecting the 100-comment limit) → error handling (comment to incident on failure).
- **Endpoints used** table (`Data Type | Method | Endpoint`), then per endpoint: Method, Endpoint, Headers,
  Body, **API Response** (use the `<API Response>` tag — see below), Status Codes.
- **Enrichment field maps** — `Field Label | Response Field Path | Data Type` tables per IOC type.

**(b) Action / SOAR playbooks** (e.g. Vectra: Close Detections, Open Detections, Download PCAP, Timeline
Update) — take a remediation/response action via the vendor API. This is the richer, deployment-heavy form.
Structure each playbook as:
- `### <PlaybookName>:` (H3). Open with one sentence on what it does, then the **workflow** as an ordered list:
  `<Playbook> runs on the incidents. First fetches Entity ID / Entity Type from the incident. Retrieves
  <inputs> from the incident comment if available. In case <comment missing/='all'>, makes an API request to
  fetch <ids>. Otherwise collects inputs from the user via an Adaptive Card (list the card fields). Performs
  the <action> API call. Checks the status. If successful, adds a comment to the incident with <details>.
  If not successful, terminates the playbook with an error.` End with a **Note** on any dependency playbook
  (e.g. "uses the existing VendorGenerateAccessToken playbook to generate and store the token in Key Vault").
- `#### Prerequisites:` (H4) — numbered list of what must exist before deploy: Key Vault name + Tenant ID
  (Directory ID), Teams Group ID + Channel ID (if Adaptive Cards), Storage Account (if files), and
  "the `<DependencyPlaybook>` must be deployed first".
- `#### Post Deployment:` (H4) — lettered sub-steps (keep this exact skeleton; it is near-identical per playbook):
  - **A. Authorize connections** — Logic App → API connections → Edit API connection → Authorize → Save (repeat per connection; Storage connection needs the access key).
  - **B. Add Access Policy in Key Vault** — copy the Logic App's system-assigned Managed Identity Object ID; add a Key Vault access policy granting Keys & Secrets to that identity and the authorizing user.
  - **C. Assign Role to Update Incident** — Log Analytics Workspace → Access control → add role assignment → **Microsoft Sentinel Contributor** to the Managed Identity / Logic App.
  - **D. Configurations in Microsoft Sentinel** — configure the Analytic Rule(s) (with **Entity Mapping**) that raise the incident; optionally an Automation rule to trigger the playbook; plus the manual-run steps (Incidents → select → Actions → Run Playbook).
- **API Requests:** — "Below API requests are used in the `<Playbook>` playbook." Then one labeled block per
  call (e.g. *Get Detection List*, *Close Detections*). **Use the same established request-detail format as
  the Data Connector endpoints** — i.e. the property/headers/parameters **tables** (Property|Value table for
  Endpoint/Method/Content-Type; a Required Headers table; a Query Parameters table for GET; a Request Body
  code block for POST/PATCH). Do **not** invent the response — close each block with `API Response:` on its
  own line followed by the `<API Response>` tag (see below). Only the *response* uses the tag; request
  details stay in the normal table format.

### `<API Response>` tag — never fabricate response bodies
Real API response JSON is large, vendor-specific, and must be accurate, so **do not invent it**. Wherever a
sample API response belongs (Data Connector endpoint samples, playbook API Requests), emit the literal
placeholder tag on its own line:
```
API Response:
<API Response>
```
The user pastes the real captured response in place of `<API Response>` after generation. The same rule
applies to any large real artifact you can't verify — prefer a clear placeholder over a plausible fake.
(Request bodies, endpoints, methods, headers, and field-mapping tables CAN be filled when known from the
API guide — only the *response payloads* default to the tag.)

## 10. MS Sentinel Limitation (H1) — when relevant
Document platform constraints that affect the design, e.g.:
- **Incident**: "It can only contain 100 comments at max. Until the 99th comment the playbook adds enrichment;
  the 100th comment notes the limit was exceeded."
- **Log Analytics Custom Table** limits, ingestion size limits (30MB/req, 30KB/field), etc.

## 11. References (H1)
Bullet list of doc links used: Azure Sentinel Solutions GitHub, Sentinel Normalization Parsers,
Azure Monitor Log Ingestion API, Azure Functions (Timer Trigger / Scale & Hosting), and the vendor's API
reference + auth reference.

---

## House phrasing snippets (reuse verbatim where they fit)
- Sentinel boilerplate: *"Microsoft Sentinel is a scalable, cloud-native, security information and event
  management (SIEM) and security orchestration, automation, and response (SOAR) solution."* + the four
  Collect/Detect/Investigate/Respond bullets.
- Data Connector intro: *"Data connector allows logs from various sources to be ingested into Sentinel.
  There are primarily five ways of ingesting the data. This integration uses …"*
- Parser intro: *"Parsers are KQL user-defined functions that normalize `<Vendor>` data and enable effective
  correlation within Microsoft Sentinel."*
- Use-cases intro and Tech-impl intro lines (see sections 8 and 9 above).

## Diagram style (house palette — match exactly)
The two architecture diagrams use a **grayscale, swim-lane** style. Keep this palette when customizing
`templates/*.drawio` or authoring new diagrams — do NOT introduce bright fills.
- **Layout**: stacked horizontal zones — Vendor (top) → "Microsoft Azure" boundary containing the Data
  Connector zone (middle) → "Microsoft Sentinel / Azure" zone (bottom, dashed). Flow goes top-to-bottom,
  left-to-right, with **circled-number labels ①②③…** on the edges in execution order.
- **Component boxes**: `rounded=1;fillColor=#f0f0f0;strokeColor=#666666;fontColor=#000000;fontSize=10;arcSize=5`.
  Title of box in `<b>…</b>`, details on following `<br/>` lines.
- **Zone group containers**: `fillColor=#e8e8e8;strokeColor=#444444;strokeWidth=1.5;arcSize=2-3`, with a
  centered bold `fontSize=12` label text cell on top.
- **Azure outer boundary**: `fillColor=none;strokeColor=#444444;strokeWidth=2`, left-aligned "Microsoft Azure" label.
- **Sentinel zone**: dashed — `fillColor=none;strokeColor=#444444;strokeWidth=1.5;dashed=1;dashPattern=8 4`.
- **Inner function container** (DC diagram): `fillColor=#f5f5f5;strokeColor=#666666;strokeWidth=1`.
- **Title**: `fontSize=14;fontStyle=1;fontColor=#000000`, centered, `<Vendor> <Product> – Microsoft Sentinel | <diagram name>`.
- **Flow edges**: `edgeStyle=orthogonalEdgeStyle;endArrow=block;endFill=1;strokeColor=#444444;fontSize=10;fontColor=#000000`.
  Sequential steps inside the function use lighter open arrows: `endArrow=open;endFill=0;strokeColor=#666666`.
- Placeholders in the templates: `{{VENDOR}}`, `{{PRODUCT}}`, `{{TABLE}}` (custom table, e.g. `Foo_CL`),
  `{{PARSER}}` (KQL function name). Replace all; drop boxes for components not in scope.

## Anti-patterns (avoid)
- Inventing API endpoints, schemas, field names, or limits. Use `<TBD>`.
- Fabricating API response JSON. Use the `<API Response>` tag so the user pastes the real payload.
- Pure high-level marketing prose with no tables or concrete mechanics.
- Over-specifying code line-by-line. The TDD is design, not source.
- Documenting components that don't exist in this integration.
