<!--
TDD skeleton. Fill every <PLACEHOLDER>. Delete sections for components not in scope.
Keep the headings "Overall System Architecture" and "Data Connector Architecture" exactly
so diagrams auto-place. Read reference/tdd-format.md for the depth contract before writing.
-->

<Vendor> <Product> Microsoft Sentinel Integration

v1.0.0

# Version Control

| # | Document Version | Date | Owner | Document Status | Comments |
|---|------------------|------|-------|-----------------|----------|
| 1 | 1.0.0 | <Month Dayth, Year> | Crest Data | Initial Draft |  |

# Overview

## Microsoft Sentinel Platform

Microsoft Sentinel is a scalable, cloud-native, security information and event management (SIEM) and security orchestration, automation, and response (SOAR) solution. Sentinel Platform provides the following capabilities:

- Collect data at cloud scale across all users, devices, applications, and infrastructure, both on-premises and in multiple clouds.
- Detect previously undetected threats, and minimize false positives using Microsoft's analytics and unparalleled threat intelligence.
- Investigate threats with artificial intelligence, and hunt for suspicious activities at scale, tapping into years of cyber security work at Microsoft.
- Respond to incidents rapidly with built-in orchestration and automation of common tasks.

## <Vendor> Platform

<2–4 sentences: what the vendor product is and the value it provides.>

## <Vendor> Microsoft Sentinel Integration

<2–4 sentences: how the integration is implemented, what data it retrieves, and the outcome in Sentinel.>

# Compatibility Matrix

Python Version : 3.12
<Vendor> API Version : <version>

# Prerequisites

Azure Account subscription (with the below services enabled / proper rights):

- Azure Subscription with owner role is required to register an application in Microsoft Entra ID and assign the contributor role.
- Function App
- Storage Account
- Log Analytics Workspace
- Microsoft Sentinel

Workspace (with the below services enabled / proper rights):

- Read and write permissions on the workspace are required.
- Microsoft.Web/sites permissions: read and write permissions to create a Function App.

REST API Credentials/Permissions:

- <Vendor> API credentials are required. See the documentation to learn more.

# System Architecture

<Integration> consists of the following components:

- Data Connector
- Parsers
- Analytic Rule
- Solution Packaging & Documentation

## Overall System Architecture

<!-- diagram: <Vendor>_Overall_Architecture.drawio is inserted here -->

## Data Connector Architecture

<!-- diagram: <Vendor>_DataConnector_Architecture.drawio is inserted here -->

# Integration use cases

This section describes the use cases provided as part of the <Vendor> – Microsoft Sentinel integration. The integration will be distributed as a <Vendor> Solution.

## Data Connector

<1–2 sentences: what it ingests and how (API-based, timer-driven).>

## Parsers

<1 sentence: what the parser(s) normalize.>

Acceptance Criteria:

- The parser must accurately parse the data without any errors.

## Analytic Rule

<1–2 sentences: what conditions raise incidents and how attributes map.>

<!-- Add Workbook / Playbook H2 entries here ONLY if in scope, each with Acceptance Criteria. -->

# Technical Implementation Details

This section describes the technical implementation details for the use cases provided as part of this integration.

## Data Connector

Data connector allows logs from various sources to be ingested into Sentinel. This integration uses <mechanism>.

### <Vendor> Alerts Data Connector

<Few sentences on the mechanism.>

#### Azure Function

<What the function does each run.>

Authentication Flow:

- <step>

Data Fetching and Filtering:

- <incremental pull strategy; user-controlled filters>

Ingestion to Microsoft Sentinel:

- <normalization + Log Ingestion API into the target table>

Checkpoint Mechanism:

- <how duplicate ingestion is avoided / continuation across runs>

Reliability and Retry Handling:

- <retry on transient failures>

##### <Endpoint name, e.g. Exchange API Key for Bearer Token>

| Property | Value |
|----------|-------|
| Endpoint | <url> |
| Method | <GET/POST> |
| Content-Type | application/json |

Query Parameters:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| <param> | <type> | <Yes/No> | <desc> |

Response Codes:

| HTTP Code | Type | Meaning |
|-----------|------|---------|
| 200 | Success | <desc> |
| 401 | Unauthorized | <desc> |

#### User Inputs

| Name | Required | Description | Default |
|------|----------|-------------|---------|
| FunctionName | Yes | Unique name for the Azure Function. | <Vendor>Func |
| Location | Yes | Azure region for the Function App. | Resource group location |
| WorkspaceName | Yes | Microsoft Sentinel Log Analytics workspace name. |  |
| AppInsightsWorkspaceResourceID | Yes | Fully qualified resource ID of the App Insights workspace. |  |

### Data Connector Trigger

<Timer/schedule description.>

### Prerequisites

Users must have the below Azure credentials:

- Tenant ID
- Client ID
- Client Secret
- Resource Group Name
- Subscription ID

### Post deployment steps

#### Steps to Obtain <Vendor> API Key

1. <step>

## Parser

### <ParserName>

<2–4 sentences: what raw data it processes and how it normalizes/flattens into the target schema.>

## Analytic Rules

<2–4 sentences: incident generation conditions and attribute/entity mapping.>

<!-- Workbook section (panels/filters tables) goes here if in scope. -->

<!-- ===========================================================================
     Playbook section — include ONLY if playbooks are in scope. Delete otherwise.
     This is the action/SOAR shape (Vectra-style). For enrichment playbooks see
     reference/tdd-format.md. NEVER fabricate response JSON — use the <API Response> tag.
=========================================================================== -->

## Playbooks

### <VendorActionName>:

This playbook <one-line purpose, e.g. closes detections associated with the incident entity>.

- <Playbook> runs on the incidents.
- First fetches Entity ID and Entity Type from the incident.
- Retrieves the <inputs> from the incident comment if available.
- In case the <inputs> are 'all' or the comment does not match, makes an API request to fetch <ids> for that entity.
- In case the comment does not match, collects the parameters below from the user via an Adaptive Card:
  - <Card field 1 (e.g. Detection Choice — multi-select)>
  - <Card field 2 (e.g. Reason)>
- Performs the <action> API call.
- Checks the status. If successful, adds a comment to the incident with <details>. If not successful, terminates the playbook with an error.

Note: This playbook uses the existing <VendorGenerateAccessToken> playbook to generate and store the token in the Key Vault.

#### Prerequisites:

1. Obtain Key Vault name and Tenant ID (Directory ID) where client credentials are stored.
2. Obtain Teams Group ID and Channel ID (if the playbook uses Adaptive Cards).
3. <Obtain Storage Account name — if the playbook stores files.>
4. Ensure the <VendorGenerateAccessToken> playbook is deployed before this playbook.

#### Post Deployment:

A. Authorize connections — Logic App → API connections → Edit API connection → Authorize → Sign in → Save (repeat per connection).
B. Add Access Policy in Key Vault — copy the Logic App's system-assigned Managed Identity Object ID; add a Key Vault access policy granting Keys & Secrets to that identity and the authorizing user.
C. Assign Role to Update Incident — Log Analytics Workspace → Access control → Add role assignment → Microsoft Sentinel Contributor → the Managed Identity / Logic App.
D. Configurations in Microsoft Sentinel — configure the Analytic Rule(s) with Entity Mapping that raise the incident; optionally an Automation rule to trigger the playbook; manual run via Incidents → select → Actions → Run Playbook.

API Requests:

Below API requests are used in the <VendorActionName> playbook.

Get <Resource> List:

| Property | Value |
|----------|-------|
| Endpoint | <path> |
| Method | GET |
| Content-Type | application/json |

Required Headers:

| Header | Value |
|--------|-------|
| Authorization | Bearer <Access Token> |

Query Parameters:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| <param> | <type> | <Yes/No> | <desc> |

API Response:
<API Response>

<Action> <Resource>:

| Property | Value |
|----------|-------|
| Endpoint | <path> |
| Method | PATCH |
| Content-Type | application/json |

Required Headers:

| Header | Value |
|--------|-------|
| Authorization | Bearer <Access Token> |

Request Body:

```
{
  "<field>": [ "<value>" ]
}
```

API Response:
<API Response>

# MS Sentinel Limitation

## Incident

- <e.g. An incident can contain at most 100 comments.>

# References

- Azure Sentinel Solutions GitHub
- Microsoft Sentinel Normalization Parsers
- Azure Monitor Log Ingestion API
- Azure Functions Timer Trigger
- Azure Functions Scale and Hosting
- <Vendor> API Reference
- <Vendor> Authentication Reference
