---
name: Generate Sentinel TDD
description: Generate a customer-ready Technical Design Document (TDD) for a new Microsoft Sentinel integration, in the team's house markdown format — Overview, Compatibility Matrix, Prerequisites, System Architecture, Integration use cases, Technical Implementation Details, MS Sentinel Limitation, References.
---

# Generate a Microsoft Sentinel TDD

You are generating a **Technical Design Document** for a new Microsoft Sentinel integration. The
output is markdown only — no `.docx` conversion, no architecture diagrams are rendered by you. The
goal depth is "required minimum detail": concrete enough that an engineer could implement from it and
a customer could review it, but not a line-by-line spec.

---

## Step 1 — Collect input from the user

Ask for whichever of these the analyst hasn't already given:

1. **Vendor / product name** and a one-line description of what it does (required).
2. **Integration purpose** — what data flows into Sentinel and why (required).
3. **Components in scope** — which of: Data Connector, Parser(s), Analytic Rule(s), Workbook(s),
   Playbook(s). Not every TDD has all five — only document what exists (required).
4. **Ingestion mechanism** — Azure Function (Python) timer-pull, Codeless Connector (CCF), or other.
5. **Key API details**, if known — base URL, auth type, main endpoint(s). If unknown, use `<TBD>`
   rather than guessing.

If the analyst points at source material (an API guide, a connector folder, sample data), prefer real
detail pulled from that over asking piecemeal — but never invent an endpoint, field, or limit that
wasn't actually given to you.

## Step 2 — Validate scope completeness

Before writing, confirm you know: what data source is being integrated, why, which components are in
scope, and the ingestion mechanism. If any of those four are missing, **stop and ask** rather than
guessing — a wrong guess in a customer-facing document is worse than an honest `<TBD>`.

## Step 3 — Write the markdown following this skeleton exactly

Fill every `<PLACEHOLDER>`. Delete whole sections for components not in scope (e.g. no Workbook H2 if
no workbook is in scope). Keep the two architecture headings named **exactly**
`Overall System Architecture` and `Data Connector Architecture` even though no diagram is rendered —
downstream tooling anchors on those names.

```markdown
<Vendor> <Product> Microsoft Sentinel Integration

v1.0.0

# Version Control

| # | Document Version | Date | Owner | Document Status | Comments |
|---|------------------|------|-------|-----------------|----------|
| 1 | 1.0.0 | <Month Dayth, Year> | Crest Data | Initial Draft |  |

# Overview

## Microsoft Sentinel Platform

Microsoft Sentinel is a scalable, cloud-native, security information and event management (SIEM) and
security orchestration, automation, and response (SOAR) solution.

## <Vendor> Platform

<2-4 sentences: what the vendor product is and the value it provides.>

## <Vendor> Microsoft Sentinel Integration

<2-4 sentences: how the integration is implemented, what data it retrieves, and the outcome in Sentinel.>

# Compatibility Matrix

Python Version : 3.12
<Vendor> API Version : <version, or <TBD>>

# Prerequisites

Azure Account subscription (with the below services enabled / proper rights):
- Azure Subscription with owner role, to register an application in Microsoft Entra ID and assign the contributor role.
- Function App
- Storage Account
- Log Analytics Workspace
- Microsoft Sentinel

REST API Credentials/Permissions:
- <Vendor> API credentials are required.

# System Architecture

<Integration> consists of the following components:
- <list only the components actually in scope>

## Overall System Architecture

<one paragraph describing the end-to-end flow; no diagram is generated here>

## Data Connector Architecture

<one paragraph describing the connector's internal flow; only if a Data Connector is in scope>

# Integration use cases

## Data Connector

<1-2 sentences: what it ingests and how.>

## Parsers

<1 sentence: what the parser(s) normalize.>

Acceptance Criteria:
- The parser must accurately parse the data without any errors.

<!-- Analytic Rule / Workbook / Playbook H2 entries here ONLY if in scope, each with Acceptance Criteria. -->

# Technical Implementation Details

## Data Connector

<mechanism-specific detail: for Azure Function, cover authentication flow, data fetching/filtering,
ingestion to Sentinel, checkpoint mechanism, retry handling, and a table of the real endpoint(s) used
(Property/Value: Endpoint, Method, Content-Type) plus any query parameters and response codes actually
known. For CCF, cover the connector definition's auth type, pagination, and table/DCR shape. Use
`<TBD>` for anything not supplied — never invent a specific endpoint path, parameter, or response code.>

## Parser

<2-4 sentences per parser: what raw data it processes and how it normalizes it.>

<!-- Analytic Rules / Workbook / Playbook technical detail here ONLY if in scope. Playbooks: never
     fabricate a third-party API request/response shape — use <TBD> if the real spec wasn't supplied. -->

# MS Sentinel Limitation

- <any platform limitation relevant to this integration, or omit the section if none apply>

# References

- Azure Sentinel Solutions GitHub
- Microsoft Sentinel Normalization Parsers
- Azure Monitor Log Ingestion API
- <Vendor> API Reference
```

### Rules

- Professional, third-person, present tense. Customer-deliverable tone — no "we will probably".
- Use tables for: Version Control, endpoint property/request/response, query parameters, response
  codes, User Inputs.
- **Never fabricate** API contracts, schemas, limits, or third-party response shapes. Use `<TBD>` for
  anything not actually supplied — this is more important than sounding complete.
- Only include a component's use-case and technical-detail sections if it's actually in scope; don't
  pad the document with sections for things that don't exist.

## Step 4 — Hand off

Tell the analyst which sections are `<TBD>` and what input would resolve them. There is no deploy step
for a TDD — once it's approved, work moves on to the Data Connector, Parser, Analytic Rule, Workbook,
and Playbook generators for the components actually in scope.
