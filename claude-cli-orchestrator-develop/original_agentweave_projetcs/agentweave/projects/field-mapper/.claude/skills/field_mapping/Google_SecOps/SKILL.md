---
name: Google SecOps Field Mapping

description: Use this skill to map fields to Google SecOps (Chronicle). Trigger when destination platform is Google SecOps or Chronicle. Handles reserved field mapping, field mapping rules, and fallback logic for unmapped fields.
---

# Google SecOps Field Mapping Skill

## Description

This skill is invoked by the **Field Mapping Agent** when the **destination platform is Google SecOps (Chronicle)**. It supplements the standard 4-tier matching strategy with Google SecOps-specific rules that must be applied throughout the mapping process.

Load this skill before processing any fields. Apply its rules **in addition to** the generic tier strategy — these rules take precedence where specified.

---

## Inputs Received from the Field Mapping Agent

| Input | Required | Description |
|---|---|---|
| `fields` | Required | An object containing `reserved_fields` and `custom_fields` lists, as provided to the Field Mapping Agent. |
| `migration_type` | Required | The type of migration: `alert` or `dashboard`. Determines the fallback destination field format for unmapped fields. |

---

## Step 1 — Map Reserved Fields

For each field in `reserved_fields`, look up the source field in the **Platform Default Fields Reference** table below and map it to the semantically equivalent Google SecOps built-in field.

### Platform Default Fields Reference

| Concept | Splunk | Google SecOps (Chronicle) |
|---|---|---|---|---|---|
| Log source type | `sourcetype` | `metadata.log_type` |
| Event timestamp | `_time` | `metadata.event_timestamp` |

### Mapping Rules

1. Identify the source platform's column in the table above.
2. Find the row where the source field appears.
3. Read the Google SecOps (Chronicle) column for the semantically equivalent destination field.
4. Verify data types are compatible.
5. **If match found**: record the mapping with `matching_tier` set to `"RESERVED"`.
6. **If no match found**: skip the mapping and do not add any mapping to the field_mapping json.

**Reasoning format**:
```
"[RESERVED] Source field 'sourcetype' is a Splunk built-in default field. The semantically equivalent field on Google SecOps is 'metadata.log_type'. Both represent the log source type."
```

---

## Step 2 — Apply Rules During Custom Field Mapping

Process each source field in `custom_fields` using the following rules:

### Rule 1: Unmapped Field Fallback *(apply instead of Tier 4)*

If a custom source field reaches **Tier 4 (Unmatched)** — no match found across Tiers 1–3 — do **not** map the destination to the same source field name. Instead, apply the following fallback based on `migration_type`:

| `migration_type` | Destination Field Value |
|---|---|
| `alert` | `$e.extracted.fields["<source_field_name>"]` |
| `dashboard` | `extracted.fields["<source_field_name>"]` |

Replace `<source_field_name>` with the actual name of the source field being mapped.

**Reasoning format**:
```
"[TIER 4 - SecOps Fallback] No standard match found across Tiers 1–3. Mapped to Google SecOps extracted fields using migration_type '<migration_type>'. Destination: $e.extracted.fields["<source_field_name>"] / extracted.fields["<source_field_name>"]."
```

**Example**:
- Source field: `custom_metric_value`
- `migration_type`: `alert`
- Destination: `$e.extracted.fields["custom_metric_value"]`

---

## Application Order Summary

For each source field, when destination platform is Google SecOps:

1. **Step 1 (once, before custom fields)** — Map all `reserved_fields` using the Platform Default Fields Reference table.
2. **Tiers 1–3** — For all `custom_fields`, apply the standard 4-tier matching strategy from the Field Mapping Agent.
3. **Rule 1** — If any custom field reaches Tier 4: map to `$e.extracted.fields["<field>"]` or `extracted.fields["<field>"]` based on `migration_type`. Never leave destination empty for Google SecOps.

---

## CRITICAL: UDM Prefix Stripping Rule

**MANDATORY for every destination field in the output JSON.**

Google SecOps parsers internally use the full UDM namespace prefix `event.idm.read_only_udm.` before field paths. This prefix is an internal implementation detail and **MUST be stripped** from all destination field values in the mapping output.

### How to Apply

When you read a destination field from the Google SecOps extraction CSV or parser code that starts with `event.idm.read_only_udm.`, **remove that prefix entirely**.

| Raw Value from Parser/CSV | Correct `destination_field` in output |
|---|---|
| `event.idm.read_only_udm.principal.user.userid` | `principal.user.userid` |
| `event.idm.read_only_udm.principal.ip` | `principal.ip` |
| `event.idm.read_only_udm.target.hostname` | `target.hostname` |
| `event.idm.read_only_udm.security_result.action` | `security_result.action` |
| `event.idm.read_only_udm.metadata.event_type` | `metadata.event_type` |
| `event.idm.read_only_udm.network.ip_protocol` | `network.ip_protocol` |

### Rules

1. **Always strip** the prefix `event.idm.read_only_udm.` from any destination field — whether it comes from RESERVED mapping, Tier 1–3 matching, or Tier 4 fallback.
2. **Never output** a destination field that starts with `event.idm.read_only_udm.` in the final JSON.
3. This applies to the `destination_field` key in the output JSON only — do not modify the `destination_extraction` or `reasoning` fields.
4. If a field path does NOT start with `event.idm.read_only_udm.`, leave it as-is (e.g., `extracted.fields["field"]` stays unchanged).
