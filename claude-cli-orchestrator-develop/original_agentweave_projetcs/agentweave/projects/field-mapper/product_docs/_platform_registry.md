# Platform Registry — Field Mapper

> **Purpose:** Single source of truth for all supported platform names, aliases, and canonical IDs.  
> Agents read this file to validate user-supplied platform names and resolve them to canonical forms.  
> To add a new platform, append a row — no agent `.md` files need to be modified.

---

## Platform Name Resolution Table

| User Input (case-insensitive) | Canonical Platform Name | Platform ID (file path key) |
|---|---|---|
| `google secops`, `secops`, `chronicle`, `google chronicle`, `google_secops` | `Google SecOps` | `google_secops` |
| `splunk` | `Splunk` | `splunk` |
| `datadog`, `dd` | `Datadog` | `datadog` |
| `elastic`, `elasticsearch`, `logstash`, `elk` | `Elastic` | `elastic` |
| `dynatrace`, `dt` | `Dynatrace` | `dynatrace` |

### How to Use

1. Receive `source_platform_name` and `destination_platform_name` from agent input
2. Match each value (case-insensitive) against the **User Input** column
3. Resolve to the **Canonical Platform Name** for display and logging
4. Resolve to the **Platform ID** for file path construction:
   - Skill path: `.claude/skills/field_extraction/{Canonical Platform Name}/SKILL.md`
   - Default fields path: `product_docs/{platform_id}/default_fields.md`
   - Extraction CSV: `output/{platform_id}_extractions.csv`

### When Platform Name Is Not Recognized

If the provided platform name does not match any row in the table above:

> "The platform name `[NAME]` is not recognized. Please check `product_docs/_platform_registry.md` for the list of supported platforms and their accepted aliases, then provide a valid platform name."

Do **not** halt both sides — continue processing the other platform if its name is valid.

---

## Platform Capability Matrix

| Platform ID | Extraction Skill | Mapping Skill | Default Fields Config |
|---|---|---|---|
| `google_secops` | `.claude/skills/field_extraction/Google SecOps/SKILL.md` | `.claude/skills/field_mapping/Google_SecOps/SKILL.md` | `product_docs/google_secops/default_fields.md` |
| `splunk` | `.claude/skills/field_extraction/Splunk/SKILL.md` | — (use generic 5-tier strategy) | `product_docs/splunk/default_fields.md` |
| `datadog` | `.claude/skills/field_extraction/Datadog/SKILL.md` | — (use generic 5-tier strategy) | `product_docs/datadog/default_fields.md` |
| `elastic` | `.claude/skills/field_extraction/Elastic/SKILL.md` | — (use generic 5-tier strategy) | `product_docs/elastic/default_fields.md` |
| `dynatrace` | `.claude/skills/field_extraction/Dynatrace/SKILL.md` | — (use generic 5-tier strategy) | `product_docs/dynatrace/default_fields.md` |

> **If a skill file is missing** for a platform: notify the user with the expected path and stop
> processing for that platform only. Continue with the other platform if its skill is available.

---

## Onboarding a New Platform

To add a new platform to the field-mapper pipeline:

1. **Add a row to this file** — Add the new platform's aliases, canonical name, and platform ID
2. **Create extraction skill** — Add `.claude/skills/field_extraction/{Canonical Name}/SKILL.md`
3. **Create default fields config** — Add `product_docs/{platform_id}/default_fields.md`
4. **Create mapping skill (optional)** — Add `.claude/skills/field_mapping/{Canonical Name}/SKILL.md` if platform-specific mapping overrides are needed
5. **No agent `.md` files need to be modified**
