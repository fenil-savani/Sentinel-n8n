---
name: Splunk Field Extractor
description: Extract fields and their extraction logic from Splunk apps by calling the Splunk MCP server tool and formatting the JSON output to CSV.
---
# Splunk Field Extraction Skill

## Description

This skill connects to the **Splunk MCP server**, calls its field extraction tool with the app name and input fields, receives the extraction results in JSON format, and writes them to a CSV file.

Unlike parser-analysis skills that read and interpret parser code, this skill **delegates the extraction work to the Splunk MCP server tool** and focuses on formatting the returned results.

---

## Inputs Received from the Field Extractor Agent

This skill is invoked by the Field Extractor Agent with the following inputs:

| Input | Description |
|---|---|
| `app_name` | The Splunk app or technology add-on whose field extractions are needed (e.g., `Splunk_TA_windows`, `Splunk_TA_okta`, `Splunk_TA_paloalto`) |
| `input_fields` | *(Optional)* A list of specific fields to extract. If not provided, extract all fields returned by the tool. |

---

## Step 1 — Call the **Splunk MCP server** to extract fields.

## Step 2 — Call the Field Extraction Tool

Call the relevant tool on the Splunk MCP server to extract fields and their extraction logic.

### Tool Input

Pass the following parameters to the tool:

| Parameter | Value |
|---|---|
| `app_name` | The `app_name` received from the Field Extractor Agent |
| `input_fields` | The `input_fields` list if provided. If not provided, the tool returns all available field extractions for the app. |

1. Call the tool once with `app_name` and `input_fields`.
2. Store the returned JSON response.

> If the tool returns an error or empty response, log the error:
> "Failed to extract fields for app `[APP_NAME]` from Splunk MCP server: [ERROR]"
> Then **stop processing** for this platform.

---

## Step 3 — Parse the JSON Response

The MCP tool returns a JSON response with the following top-level structure:

```json
{
  "status": "success",
  "app_name": "<app name>",
  "total_extractions": <number>,
  "input_fields": ["<field1>", "<field2>", ...],
  "matched_extractions_count": <number>,
  "matched_extractions": [ ... ]
}
```

The `matched_extractions` array contains objects in the following format:

```json
{
  "sourcetype/source": "<Splunk sourcetype or source string>",
  "field": "<field name, may include alias notation like Name:user>",
  "extraction_type": "<eval | lookup | fieldalias | regex | regex_transform | ...>",
  "extraction": "<the full extraction expression, eval formula, lookup definition, or alias rule>",
  "format_groups": "<grouping metadata, may be empty string>"
}
```

Extract the `matched_extractions` array from the response. For every object in this array, extract all five values.

### Handling input_fields Filter

- The MCP tool already filters extractions based on the `input_fields` provided. Include **every object** from the `matched_extractions` array in the CSV output.
- Do **not** apply any additional filtering — use the `matched_extractions` as-is.

---

## Step 4 — Write CSV Output

Write results into a CSV file. Do not print results as plain text. Do not modify any keys or values from the received response.

The Field Extractor Agent specifies the output path. This skill produces the CSV content with the following columns, mapped directly from the JSON response fields:

```
Sourcetype/Source,Field,Extraction Type,Extraction Logic,Format Groups
```

### Column Mapping from JSON to CSV

| CSV Column | JSON Key | Description |
|---|---|---|
| `Sourcetype/Source` | `sourcetype/source` | The Splunk sourcetype or source string |
| `Field` | `field` | The field name (may include alias notation like `Name:user`) |
| `Extraction Type` | `extraction_type` | The type of extraction: `eval`, `lookup`, `fieldalias`, `regex`, etc. |
| `Extraction Logic` | `extraction` | The full extraction expression, eval formula, lookup definition, or alias rule, exactly as returned by the tool |
| `Format Groups` | `format_groups` | Grouping metadata. Leave empty if the JSON value is an empty string. |

### CSV Encoding and Quoting Rules

- Use **UTF-8 encoding**.
- **Wrap any field value in double quotes** if it contains commas, newlines, or double-quote characters.
- Escape internal double-quote characters by **doubling them** (`""`).
- Each JSON object maps to **one CSV row**. If the same field name appears multiple times (different sourcetypes or extraction types), each becomes a separate row.
- If no extraction is found for a requested field, set `Extraction Logic` to `No extraction found` and leave other columns empty.

---

## Example

### JSON Response from MCP Tool

```json
{
  "status": "success",
  "app_name": "Splunk_TA_windows",
  "total_extractions": 1098,
  "input_fields": ["action", "user"],
  "matched_extractions_count": 5,
  "matched_extractions": [
    {
      "sourcetype/source": "source::XmlWinEventLog:Security",
      "field": "user",
      "extraction_type": "eval",
      "extraction": "case(EventCode==4794,\"DSRM administrator\",EventCode IN (4727,4730,4731,4734,4735,4737,4754,4755,4758,4764),null(),EventCode==4688,if(user==\"-\" OR isnull(user),src_user,user),EventCode IN (1102,4672,4673,4674,4689,4697,4698,4700,4701,4702,4706,4713,4719,4744,4749,4750,4759,4799,4876), case(SubjectUserName!=\"-\",SubjectUserName),EventCode==4696,case(user!=\"-\",user),EventCode IN (4703,4704,4705,4720,4722,4723,4724,4725,4726,4738,4767,4798), TargetUserName, EventCode==4781, NewTargetUserName, EventCode IN (4728, 4729, 4732, 4733, 4756, 4757), if(like(MemberSid, \"%\\%\"), mvindex(split(MemberSid, \"\\\\\"),-1), if(like(member_user_name, \"%\\%\"), null(), member_user_name)), EventCode IN (5156,5157), RemoteMachineID, true(), user)",
      "format_groups": ""
    },
    {
      "sourcetype/source": "source::XmlWinEventLog:System",
      "field": "action",
      "extraction_type": "eval",
      "extraction": "case(EventCode==104, \"cleared\")",
      "format_groups": ""
    },
    {
      "sourcetype/source": "WindowsFirewallLog",
      "field": "action",
      "extraction_type": "lookup",
      "extraction": "action_lookup win_action AS win_action OUTPUTNEW action AS action",
      "format_groups": ""
    },
    {
      "sourcetype/source": "WMI:UserAccounts",
      "field": "Name:user",
      "extraction_type": "fieldalias",
      "extraction": "Name AS user",
      "format_groups": ""
    },
    {
      "sourcetype/source": "WMI:WinEventLog:Application",
      "field": "User:user",
      "extraction_type": "fieldalias",
      "extraction": "User AS user",
      "format_groups": ""
    }
  ]
}
```

### CSV Output

```csv
Sourcetype/Source,Field,Extraction Type,Extraction Logic,Format Groups
source::XmlWinEventLog:Security,user,eval,"case(EventCode==4794,""DSRM administrator"",EventCode IN (4727,4730,4731,4734,4735,4737,4754,4755,4758,4764),null(),EventCode==4688,if(user==""-"" OR isnull(user),src_user,user),EventCode IN (1102,4672,4673,4674,4689,4697,4698,4700,4701,4702,4706,4713,4719,4744,4749,4750,4759,4799,4876), case(SubjectUserName!=""-"",SubjectUserName),EventCode==4696,case(user!=""-"",user),EventCode IN (4703,4704,4705,4720,4722,4723,4724,4725,4726,4738,4767,4798), TargetUserName, EventCode==4781, NewTargetUserName, EventCode IN (4728, 4729, 4732, 4733, 4756, 4757), if(like(MemberSid, ""%\%""), mvindex(split(MemberSid, ""\\""),-1), if(like(member_user_name, ""%\%""), null(), member_user_name)), EventCode IN (5156,5157), RemoteMachineID, true(), user)",
source::XmlWinEventLog:System,action,eval,"case(EventCode==104, ""cleared"")",
WindowsFirewallLog,action,lookup,action_lookup win_action AS win_action OUTPUTNEW action AS action,
WMI:UserAccounts,Name:user,fieldalias,Name AS user,
WMI:WinEventLog:Application,User:user,fieldalias,User AS user,
```

---

## Output Rules

Follow these rules strictly:

1. Do not modify or summarize the extraction logic returned by the tool — preserve it exactly.
2. Do not invent or fabricate extraction logic — only use what the MCP tool returns.
3. Ignore the top-level metadata keys (`status`, `app_name`, `total_extractions`, `input_fields`, `matched_extractions_count`) for CSV output — only use the `matched_extractions` array.
4. Maintain the order of fields as returned by the tool.

---

## Important Guidelines

- This skill **does not** parse Splunk `.conf` files directly. It relies entirely on the Splunk MCP server tool to perform the extraction.
- The accuracy of the output depends on the MCP tool's response. If the response format differs from the expected JSON structure, adapt the parsing accordingly and document any assumptions.
- Always verify the connection to the MCP server before calling the tool.
