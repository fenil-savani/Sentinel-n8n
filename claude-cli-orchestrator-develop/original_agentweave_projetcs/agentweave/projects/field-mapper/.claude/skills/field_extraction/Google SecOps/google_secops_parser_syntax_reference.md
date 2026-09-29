# Google SecOps Parser Syntax Reference

> **Source:** [Google Cloud – Parser syntax reference](https://docs.cloud.google.com/chronicle/docs/reference/parser-syntax)
>
> This document is a complete reference for the functions, parsing patterns, and syntax supported in Google Security Operations (Chronicle) data mapping instructions.

---

## 1. Introduction

Google Security Operations (formerly Chronicle) uses **parsers** to transform raw log data into the **Unified Data Model (UDM)** format. Parsers are written using a **Logstash-style configuration syntax** (similar but not identical to Logstash). There are three types of parsers:

- **Default parsers** — provided by Google for common log sources.
- **Customer-specific parsers** — custom parsers built for unique log formats.
- **Code snippet parser extensions** — small parser additions that extend default parsers.

Each UDM record is categorized by a **UDM event type**. Each event type has **required and optional fields**. For mandatory fields per event type, see: [Required and optional fields for each event type](https://docs.cloud.google.com/chronicle/docs/unified-data-model/udm-usage).

For a conceptual overview, see: [Overview of parsing](https://docs.cloud.google.com/chronicle/docs/event-processing/parsing-overview).

---

## 2. Parser Structure Overview

A Chronicle parser follows a general structure consisting of a **filter** block that contains:

- **Data extraction** — Extract fields from raw logs using Grok, JSON, XML, KV, or CSV functions.
- **Data transformation** — Transform extracted fields using the `mutate` plugin and other functions.
- **Conditional logic** — Apply `if`/`else if`/`else` blocks to handle different log formats or values.
- **Date handling** — Parse and normalize timestamps using the `date` function.
- **UDM mapping** — Assign extracted/transformed values to UDM fields.
- **Output generation** — Merge the event into the `@output` array to produce UDM records.

### General Parser Structure

```
filter {
  # Step 1: Extract data from raw log
  <extraction_function> {
    source => "message"
    ...
  }

  # Step 2: Transform data
  mutate {
    <transform_operation> => { ... }
  }

  # Step 3: Conditional logic
  if [field] == "value" {
    mutate {
      replace => { "udm_field" => "mapped_value" }
    }
  }

  # Step 4: Parse date
  date {
    match => ["timestamp_field", "date_format"]
  }

  # Step 5: Set event type
  mutate {
    replace => {
      "event.idm.read_only_udm.metadata.event_type" => "GENERIC_EVENT"
    }
  }

  # Step 6: Output
  mutate {
    merge => { "@output" => "event" }
  }
}
```

---

## 3. Parser Processing Flow

```
Raw Log → Extraction → Field Transformation → Conditional Logic → Date Parsing → UDM Mapping → Output
```

| Stage | Description |
|---|---|
| **Raw Log** | The original log message arrives as the `message` field. |
| **Extraction** | Parse the log using Grok, JSON, XML, KV, or CSV to extract tokens. |
| **Field Transformation** | Use `mutate` operations (rename, replace, convert, merge, etc.) to transform values. |
| **Conditional Logic** | Apply `if`/`else` blocks to handle different log variations. |
| **Date Parsing** | Normalize timestamps using the `date` function. |
| **UDM Mapping** | Assign values to `event.idm.read_only_udm.*` fields. |
| **Output** | Merge event(s) into `@output` to produce UDM records. |

---

## 4. Extract Data Using the Grok Function

Grok allows you to use [predefined patterns](https://github.com/elastic/logstash/blob/v1.4.2/patterns/grok-patterns) and regular expressions to match log messages, and extract values from the log message into tokens. Grok data extraction requires that field labels are defined as part of the data extraction process.

### 4.1 Syntax for Predefined Patterns in Grok

```
%{pattern:token}
```

**Example:**

```
%{IP:hostip} %{NUMBER:event_id}
```

### 4.2 Syntax for Regular Expressions in Grok

The following regex pattern examples can be used to extract values from log messages.

```
(?P<token>regex_pattern) (?P<eventId>\\S+)
```

### 4.3 Sample Log Message and Grok Patterns

**Example original raw log:**

```
Mar 15 11:08:06 hostdevice1: FW-112233: Accepted connection TCP 10.100.123.45/9988 to 8.8.8.8/53
```

**Grok pattern to extract data from the log:**

```
%{SYSLOGTIMESTAMP:when} %{DATA:deviceName}: FW-%{INT:messageid}: (?P<action>Accepted|Denied) connection %{WORD:protocol} %{IP:srcAddr}/%{INT:srcPort} to %{IP:dstAddr}/%{INT:dstPort}
```

**Tokens and values extracted:**

| Token | Value |
|---|---|
| `when` | `Mar 15 11:08:06` |
| `deviceName` | `hostdevice1` |
| `messageid` | `112233` |
| `action` | `Accepted` |
| `protocol` | `TCP` |
| `srcAddr` | `10.100.123.45` |
| `srcPort` | `9988` |
| `dstAddr` | `8.8.8.8` |
| `dstPort` | `53` |

### 4.4 Grok Extraction Syntax

```
grok {
  match => { "message" => "<grok pattern>" }
}
```

### 4.5 Grok Overwrite Option

The `overwrite` option used with the Grok syntax allows you to overwrite a field that already exists. This feature can be used to replace a default value with the value extracted by the Grok pattern.

```
If [field] != "" {
  mutate {
    replace => { "udm_mapping" => "%{field}" }
    on_error => "field_empty"
  }
}

grok {
  match => { "message" => ["(?P<fieldName>.*)"] }
  overwrite => ["fieldName"]
  on_error => "grok_Failure"
}
```

---

## 5. Extract JSON Formatted Logs

### 5.1 JSON Extraction Syntax

```
json {
  source => "message"
  on_error => "json_failure"
}
```

### 5.2 Manipulating JSON Arrays

JSON arrays can be accessed by adding an `array_function` parameter.

```
json {
  source => "message"
  on_error => "json_failure"
  array_function => "split_columns"
}
```

The `split_columns` function makes elements of an array accessible through an index. For example, given:

```json
{ "ips" : ["1.2.3.4","1.2.3.5"] }
```

You can access the two values using `ips.0` and `ips.1` tokens.

**Nested array example:**

```json
{ "devices": [ { "ips": ["1.2.3.4"] } ] }
```

Access the IP address using `devices.0.ips.0`. Because `devices.1` doesn't exist, it will behave the same as other non-existing elements of JSON.

### 5.3 Rules for Non-Existing JSON Elements

If an element in a JSON doesn't exist, then:

- You **cannot** access it using an `if` statement unless you initialize the token to an empty string `""` before calling the JSON filter.
- You **cannot** use it in the mutate plugin's `replace` filter because it will cause an error.
- You **can** use it in the mutate plugin's `rename` filter because these will be ignored.
- You **can** use it in the mutate plugin's `merge` filter because these will be ignored.

**Example using merge with non-existing elements:**

```
mutate {
  merge => { "event.idm.read_only_udm.observer.ip" => "ips.0" }
}
mutate {
  merge => { "event.idm.read_only_udm.observer.ip" => "ips.1" }
}
# this doesn't fail even though this element doesn't exist.
mutate {
  merge => { "event.idm.read_only_udm.observer.ip" => "ips.2" }
}
# this doesn't fail even though this element doesn't exist.
mutate {
  merge => { "event.idm.read_only_udm.observer.ip" => "ips.3" }
}
```

---

## 6. Extract XML Formatted Logs

### 6.1 XML Extraction Syntax

Define the path to the field in the original log using [XPath expression syntax](https://en.wikipedia.org/wiki/XPath).

```
xml {
  source => "message"
  xpath => {
    "/Event/System/EventID" => "eventId"
    "/Event/System/Computer" => "hostname"
  }
}
```

### 6.2 Manipulating XML with Iteration

**Sample XML log:**

```xml
<Event>
  <HOST_LIST>
    <HOST>
      <ID>iD1</ID>
      <IP>iP1</IP>
    </HOST>
    <HOST>
      <ID>iD2</ID>
      <IP>iP2</IP>
    </HOST>
  </HOST_LIST>
</Event>
```

**Iterating over XML elements using a for loop:**

```
for index, _ in xml(message, /Event/HOST_LIST/HOST) {
  xml {
    source => "message"
    xpath => {
      "/Event/HOST_LIST/HOST[%{index}]/ID" => "IDs"
      "/Event/HOST_LIST/HOST[%{index}]/IP" => "IPs"
    }
  }
}
```

> **Note:** The index starts with **1** for XML iteration.

**Nested for loop example:**

Given XML with nested `<Hashes>` elements:

```xml
<Event>
  <HOST_LIST>
    <HOST>
      <ID>id1</ID>
      <IP>ip1</IP>
      <Hashes>
        <Hash>hash1</Hash>
        <Hash>hash2</Hash>
      </Hashes>
    </HOST>
    <HOST>
      <ID>id2</ID>
      <IP>ip2</IP>
      <Hashes>
        <Hash>hash1</Hash>
        <Hash>hash2</Hash>
      </Hashes>
    </HOST>
  </HOST_LIST>
</Event>
```

```
for index, _ in xml(message, /Event/HOST_LIST/HOST) {
  xml {
    source => "message"
    xpath => {
      "/Event/HOST_LIST/HOST[%{index}]/ID" => "IDs"
    }
  }
  for i, _ in xml(message, /Event/HOST_LIST/HOST[%{index}]/Hashes/Hash) {
    xml {
      source => "message"
      xpath => {
        "/Event/HOST_LIST/HOST[%{index}]/Hashes/Hash[%{i}]" => "data"
      }
    }
  }
}
```

---

## 7. Extract Key-Value Formatted Logs

### 7.1 Key-Value Extraction Syntax

```
kv {
  source => "message"
  field_split => "|"
  value_split => ":"
  whitespace => "strict"
  trim_value => "\""
}
```

### 7.2 KV Filter Options

| Option | Description |
|---|---|
| **`field_split`** | The delimiter that separates each key-value pair (e.g., extracting parameters from a URL query string). |
| **`value_split`** | The delimiter between the key and the value. |
| **`whitespace`** | Handles acceptance of unnecessary whitespace around the key/value pair. Default is `lenient` (ignores surrounding whitespace). Set to `"strict"` if whitespace should not be ignored. |
| **`trim_value`** | Removes extraneous leading and trailing characters from the value, such as quotation marks. |

### 7.3 Key-Value Extraction Example

```
# initialize the token
mutate {
  replace => { "destination" => "" }
}

# use the kv filter to split the log.
kv {
  source => "message"
  field_split => " "
  trim_value => "\""
  on_error = "kvfail"
}

# assigned one of the field values to a UDM field
mutate {
  replace => {
    "event.idm.read_only_udm.target.hostname" => "%{destination}"
  }
}
```

---

## 8. Extract CSV Formatted Logs

The `csv` filter parses a CSV-formatted message into individual column variables (`column1`, `column2`, `column3`, etc.).

```
# parse the message into individual variables, identified as column1, column2, column3, etc.
csv {
  source => "message"
  separator => ","
  on_error => "csv_failed"
}

# assign each value to a token
mutate {
  replace => {
    "resource_id" => "%{column1}"
    "principal_company_name" => "%{column3}"
    "location" => "%{column4}"
    "transaction_amount" => "%{column6}"
    "status" => "%{column9}"
    "meta_description" => "%{column11}"
    "target_userid" => "%{column24}"
    "target_company_name" => "%{column13}"
    "principal_userid" => "%{column15}"
    "date" => "%{column16}"
    "time" => "%{column17}"
  }
}
```

---

## 9. Loop Over a JSON Array Using a For Loop

You can use a `for` loop to iterate over a JSON array.

### 9.1 Basic For Loop Syntax

```
for <item> in <array> {
  ...
}
```

### 9.2 Loop Over an Array

**Sample log containing a `businessPhones` array:**

```json
{
  "data": {
    "businessPhones": [
      "(123) 234-2320",
      "(123) 234-2321"
    ]
  }
}
```

**Parser code using `for` to iterate over phone numbers:**

```
filter {
  json {
    source => "message"
    on_error => "json_failure
    array_function => "split_columns"
  }

  for phoneNumber in businessPhones {
    mutate {
      merge => {
        "event.idm.read_only_udm.principal.resource.attribute.labels" => "phoneNumber"
      }
    }
  }
}
```

**Statedump output (abbreviated):**

```json
"event": {
  "idm": {
    "read_only_udm": {
      "principal": {
        "resource": {
          "attribute": {
            "labels": [
              "(123) 234-2320",
              "(123) 234-2321"
            ]
          }
        }
      }
    }
  }
}
```

### 9.3 Get the Index of an Array

You can get the index value of an array. The value of the index starts with **0**.

```
for index, <item> in <array> {
  ...
}
```

**Example — extracting index of each element in `businessPhones`:**

```
filter {
  json {
    source => "message"
    on_error => "json_failure
    array_function => "split_columns"
  }

  for index, phoneNumber in businessPhones {
    mutate {
      convert => { "index" => "string" }
    }
    mutate {
      replace => { "phoneNumber_label" => "" }
    }
    mutate {
      replace => {
        "phoneNumber_label.key" => "phoneNumber %{index}"
        "phoneNumber_label.value" => "%{phoneNumber}"
      }
      on_error => "phoneNumber_invalid"
    }
    if ![phoneNumber_invalid] {
      mutate {
        merge => {
          "event.idm.read_only_udm.principal.resource.attribute.labels" => "phoneNumber_label"
        }
        on_error => "phoneNumber_label_merge_failed"
      }
    }
    statedump {}
  }

  mutate {
    replace => {
      "event.idm.read_only_udm.metadata.event_type" => "GENERIC_EVENT"
    }
  }
  mutate {
    merge => { "@output" => "event" }
  }
}
```

**Statedump output (abbreviated):**

```json
"event": {
  "idm": {
    "read_only_udm": {
      "principal": {
        "resource": {
          "attribute": {
            "labels": [
              {
                "key": "phoneNumber 0",
                "value": "(123) 234-2320"
              },
              {
                "key": "phoneNumber 1",
                "value": "(123) 234-2321"
              }
            ]
          }
        }
      }
    }
  }
}
```

### 9.4 Access Nested Arrays

You can use a nested `for` loop to access nested arrays.

**Sample log with nested arrays:**

```json
{
  "records": {
    "hostname": "host"
  },
  "resourceIdentifiers": [
    {
      "tentantid": "a123",
      "type": "access",
      "subnet": [
        { "ip": "10.1.1.1" },
        { "ip": "10.1.1.2" }
      ]
    }
  ]
}
```

**Parser code with nested for loops:**

```
filter {
  json {
    source => "message"
    on_error => "json_failure
    array_function => "split_columns"
  }

  for index, resourceId in resourceIdentifiers {
    for key, value in resourceId map {
      mutate {
        replace => { "resId_map_label" => "" }
      }
      if [key] != "" {
        mutate {
          replace => {
            "resId_map_label.key" => "resourceId %{key}"
          }
          on_error => "key_invalid"
        }
        if ![key_invalid] and [value] != "" {
          mutate {
            replace => {
              "resId_map_label.value" => "%{value}"
            }
            # Because the key with the name "subnet" is an array, it will produce an error.
            # Hence, the value inside the "subnet" key will not show in the output.
            # To map the array values inside the "subnet" key, we need to run one more
            # for loop over the value of the "subnet" key.
            on_error => "value_nested"
          }
          if ![value_nested] {
            mutate {
              merge => {
                "event.idm.read_only_udm.principal.resource.attribute.labels" => "resId_map_label"
              }
              on_error => "resId_map_label_merge_failed"
            }
          } else {
            for sub_index, subnet in value {
              mutate {
                convert => { "sub_index" => "string" }
              }
              for subnet_key, subnet_value in subnet map {
                mutate {
                  replace => { "subnet_map_label" => "" }
                }
                mutate {
                  replace => {
                    "subnet_map_label.key" => "%{key} %{subnet_key} %{sub_index}"
                    "subnet_map_label.value" => "%{subnet_value}"
                  }
                  on_error => "subnet_value_invalid"
                }
                if ![subnet_value_invalid] {
                  mutate {
                    merge => {
                      "event.idm.read_only_udm.principal.resource.attribute.labels" => "subnet_map_label"
                    }
                    on_error => "subnet_map_label_merge_failed"
                  }
                }
              }
            }
            statedump {}
          }
        }
      }
    }
  }

  mutate {
    replace => {
      "event.idm.read_only_udm.metadata.event_type" => "GENERIC_EVENT"
    }
  }
  mutate {
    merge => { "@output" => "event" }
  }
}
```

**Statedump output (abbreviated):**

```
events_for_log_entry: {
  events: {
    timestamp: { seconds: 1709619033  nanos: 818679197 }
    idm: {
      read_only_udm: {
        metadata: {
          event_timestamp: { seconds: 1709619033  nanos: 818679197 }
          event_type: GENERIC_EVENT
        }
        principal: {
          resource: {
            attribute: {
              labels: { key: "subnet ip 0"  value: "10.1.1.1" }
              labels: { key: "subnet ip 1"  value: "10.1.1.2" }
              labels: { key: "resourceId tentantid"  value: "a123" }
              labels: { key: "resourceId type"  value: "access" }
            }
          }
        }
      }
    }
  }
}
```

**Key aspects of nested array access:**

- **Outer for loop** — `for index, resourceId in resourceIdentifiers {...}`: Iterates over the `resourceIdentifiers` array containing resource identifier objects.
- **Inner for loop** — `for key, value in resourceId map {...}`: Iterates over each key-value pair within each `resourceId` object using the `map` keyword.
- **Create labels** to store key-value pairs using the `replace` function.
- **Handle potential errors** using `on_error` flags (e.g., `key_invalid` and `value_nested`).
- **Iterate over nested subnet array** — `for sub_index, subnet in value {...}`: Iterates over the `subnet` key containing an array of subnet objects. Each key-value pair is then iterated using `for subnet_key, subnet_value in subnet map {...}`.
- **Merge transformed data** using the `merge` function into `event.idm.read_only_udm.principal.resource.attribute.labels`.

---

## 10. Loop Over Key-Value Pairs of a JSON Object Using Map

You can use the `map` keyword to loop over key-value pairs of a JSON object.

### 10.1 Map Syntax

```
for key, value in <object> map {
  ...
}
```

Both `key` and `value` are of the `string` data type.

### 10.2 Loop Over Key-Value Pairs of an Object

**Sample log — Kubernetes resource:**

```json
{
  "resource": {
    "type": "k8s_container",
    "labels": {
      "container_name": "test-container",
      "namespace_name": "default",
      "location": "us-west1-a",
      "project_id": "abc-123",
      "cluster_name": "test-cluster",
      "pod_name": "test-pod-123"
    }
  }
}
```

**Parser code using `map` to iterate over `resource.labels`:**

```
filter {
  json {
    source => "message"
    on_error => "json_failure
    array_function => "split_columns"
  }

  for key, value in resource.labels map {
    mutate {
      replace => {
        "test.key" => "%{key}"
        "test.value" => "%{value}"
      }
    }
    statedump {}
  }
}
```

**Output for each iteration:**

First Iteration:
```json
"test": { "key": "cluster_name", "value": "test-cluster" }
```

Second Iteration:
```json
"test": { "key": "container_name", "value": "test-container" }
```

Third Iteration:
```json
"test": { "key": "location", "value": "us-west1-a" }
```

Fourth Iteration:
```json
"test": { "key": "namespace_name", "value": "default" }
```

Fifth Iteration:
```json
"test": { "key": "pod_name", "value": "test-pod-123" }
```

Sixth Iteration:
```json
"test": { "key": "project_id", "value": "abc-123" }
```

### 10.3 Loop Over Key-Value Pairs of a Nested Object

You can use the `map` keyword to loop over key-value pairs of nested objects.

**Sample log with nested `location` object:**

```json
{
  "resource": {
    "type": "k8s_container",
    "labels": {
      "container_name": "test-container",
      "namespace_name": "default",
      "location": {
        "code": "us-west1-a",
        "country": "US"
      },
      "cluster_name": "test-cluster",
      "pod_name": "test-pod-123"
    }
  }
}
```

**Parser code to iterate over nested key-value pairs:**

```
filter {
  json {
    source => "message"
    on_error => "json_failure
    array_function => "split_columns"
  }

  for key, value in resource.labels map {
    mutate {
      replace => { "test.key" => "%{key}" }
    }
    mutate {
      replace => { "test.value" => "%{value}" }
      on_error => "nested_key"
    }
    if [test][key] == "location" {
      for nestedKey, nestedValue in value map {
        mutate {
          replace => {
            "locationLabel.key" => "%{nestedKey}"
            "locationLabel.value" => "%{nestedValue}"
          }
        }
        statedump {}
      }
    }
  }
}
```

**Statedump output for `resource.labels.location`:**

```json
"key": "location",
"locationLabel": { "key": "code", "value": "us-west1-a" }
...
"locationLabel": { "key": "country", "value": "US" }
```

---

## 11. Transform Data Using the Mutate Plugin

Use the `mutate` filter plugin to transform and consolidate data into a single block or to break the data into separate mutate blocks. When using a single block for the mutate functions, be aware that the mutations are executed in the order described in the [Logstash mutate plugin documentation](https://www.elastic.co/guide/en/logstash/current/plugins-filters-mutate.html).

### 11.1 Convert Function

Use the `convert` function to transform values to different data types. This conversion is needed to assign values into fields within the respective data type schemas.

**Supported data types:**

| Data Type | Description |
|---|---|
| `boolean` | Boolean true/false |
| `float` | Floating point number |
| `hash` | Hash value |
| `integer` | Integer number |
| `ipaddress` | IP address |
| `macaddress` | MAC address |
| `string` | String |
| `uinteger` | Unsigned integer |
| `hextodec` | Hexadecimal to decimal conversion |
| `hextoascii` | Hexadecimal to ASCII conversion |

**Convert example:**

```
mutate {
  convert => { "jsonPayload.packets_sent" => "uinteger" }
}
```

> **Note:** Legacy proto definitions (EDR, Webproxy, etc.) require data type conversions to match the target data type. UDM allows for handling of most fields as strings, including IP address fields.

### 11.2 Gsub Function

Match a regular expression against a field value and replace all matches with a replacement string. This applies only to string fields.

**Gsub syntax:** The configuration takes an array consisting of 3 elements per field/substitution — the field name, the regular expression to replace, and the substitution string.

The `gsub` function supports [re2 syntax](https://github.com/google/re2/wiki/Syntax). Simple strings can be used most of the time as long as they don't contain characters with special meaning (e.g., `[` or `]`). Special characters must be escaped with a backslash (`\`).

> **Important:** To search for a literal backslash, you need four backslashes (`\\\\`).

```
mutate {
  gsub => [
    # replace all occurrences of the three letters "cat" with the three letters "dog"
    "fieldname1", "cat", "dog",
    # replace all forward slashes with underscore
    "fieldname2", "/", "_",
    # replace backslashes, question marks, hashes, and minuses
    # with a dot "."
    "fieldname3", "[\\\\?#-]", "."
  ]
}
```

### 11.3 Lowercase Function

The `lowercase` function transforms a value into a lowercase value.

**Syntax:**

```
mutate {
  lowercase => [ "token" ]
}
```

**Example:**

```
mutate {
  lowercase => [ "protocol" ]
}
```

### 11.4 Merge Function

The `merge` function joins multiple fields. When parsing repeated fields (such as `ip_address` fields), use the merge function to assign IP addresses to the token. Additionally, the merge function is used to generate the normalized output message that is ingested in Google SecOps and can be used to generate multiple events from the same log line.

**Syntax:**

```
mutate {
  merge => { "destinationToken" => "addedToken" }
}
```

**Example — using a repeated field:**

```
mutate {
  merge => { "event.idm.read_only_udm.target.ip" => "dstAddr" }
}
```

**Example — output to a UDM record:**

```
mutate {
  merge => { "@output" => "event" }
}
```

### 11.5 Rename Function

The `rename` function renames a token and assigns the value to a new token. Use this function when the tokenized value can be directly assigned to the schema defined token. The original token and new token must be of the same data type. The original token is destroyed and replaced with the new token.

**Syntax:**

```
mutate {
  rename => { "originalToken" => "newToken" }
}
```

**Example:**

```
mutate {
  rename => {
    "proto" => "event.idm.read_only_udm.network.ip_protocol"
    "srcport" => "event.idm.read_only_udm.network.target.port"
  }
}
```

### 11.6 Replace Function

The `replace` function assigns a value to a token. The assignment can be based on constants, existing field values, or a combination of values. The replace function can also be used to define a token declaration. This function can only be used for **string values**.

**Syntax — assign a constant:**

```
mutate {
  replace => { "token" => "newConstantValue" }
}
```

**Syntax — assign a variable value:**

```
mutate {
  replace => { "token" => "%{otherTokenValue}" }
}
```

**Example — assign a constant:**

```
mutate {
  replace => {
    "event.idm.read_only_udm.security_result.action" => "ALLOW"
  }
}
```

**Example — assign a variable value:**

```
mutate {
  replace => { "shost" => "%{dhost}" }
}
```

### 11.7 Uppercase Function

The `uppercase` function transforms a value into an uppercase value.

**Syntax:**

```
mutate {
  uppercase => [ "token" ]
}
```

**Example:**

```
mutate {
  uppercase => [ "protocol" ]
}
```

### 11.8 RemoveField Function

The `remove_field` function destroys a token. The name of the token to be destroyed can be either static or dynamic using existing token values. No action is performed if the token doesn't exist.

**Syntax — remove a static token:**

```
mutate {
  remove_field => [ "token" ]
}
```

**Syntax — remove a dynamic token:**

```
mutate {
  remove_field => [ "%{someTokenValue}" ]
}
```

**Example — remove a static token:**

```
mutate {
  remove_field => [ "event.webproxy.protocol" ]
}
```

**Example — remove a dynamic token:**

```
mutate {
  remove_field => [ "network.%{application_protocol}" ]
}
```

### 11.9 Copy Function

The `copy` function deep copies the value of a source token into a destination token. There is no restriction on the type of value that can be copied. After copying, any change to the destination token's value will have no effect on the source token's value and vice-versa because the value is deep copied.

**Rules:**
- The source token must exist before applying the copy function.
- If the destination token does not exist, a new token is created; otherwise, the old value is overridden.

**Syntax:**

```
mutate {
  copy => { "destinationToken" => "sourceToken" }
}
```

### 11.10 Split Function

The `split` function splits a string into an iterable array.

**Syntax:**

```
mutate {
  split => {
    source => "src_field"
    separator => ","
    target => "target_field"
  }
}
```

---

## 12. Transform Data Using Other Functions

### 12.1 Base64 Function

The `base64` function converts a base64 encoded value to a string. This function is based on the Go language `base64` package.

- The `source` field identifies the variable where the input value is stored.
- The `target` field identifies the variable where to store the output.
- By default, the function uses **Standard** decoding, but can be configured to use **URL** decoding.

**Syntax:**

```
base64 {
  source => "ip_address"
  target => "ip_address_string"
  encoding => "RawStandard"
}
```

**Example — decode a base64 encoded IP address:**

```
if [ip_address] != "" {
  base64 {
    source => "ip_address"
    target => "ip_address_string"
  }
  mutate {
    merge => {
      "event.idm.read_only_udm.target.ip" => "%{ip_address_string}"
    }
  }
}
```

### 12.2 Date Function

The `date` function is required to handle the date and timestamp from the log extraction. UDM fields that store a Timestamp require a properly normalized date value. The date function supports a variety of date formats, including ISO8601, UNIX, and others along with custom-defined date and time formats.

#### Supported Predefined Date Formats

| Format | Example |
|---|---|
| `ISO 8601` | `2022-07-27T22:46:30.312Z` |
| `RFC 3339` | `2022-07-27T22:46:30.312Z` |
| `UNIX` | `1658961990` |
| `UNIX_MS` | `1658961990000` |
| `TIMESTAMP_ISO8601` | ISO 8601 timestamp |
| `yyyy-MM-dd HH:mm:ss` | `2022-07-27 22:46:30` |
| `yyyy/MM/dd HH:mm:ss` | `2022/07/27 22:46:30` |
| `yyyy-MM-ddTHH:mm:ss` | `2022-07-27T22:46:30` |
| `yyyy-MM-dd HH:mm:ss Z` | `2022-07-27 22:46:30 +0000` |
| `yyyy-MM-dd HH:mm:ss.SSS` | `2022-07-27 22:46:30.312` |
| `dd/MMM/yyyy:HH:mm:ss Z` | `27/Jul/2022:22:46:30 +0000` |
| `yyyy-MM-dd HH:mm:ss ZZ` | `2022-07-27 22:46:30 -07:00` |
| `HH:mm:ss` | `22:46:30` |
| `yyyy-MM-ddTHH:mm:ss.SSSZ` | `2022-07-27T22:46:30.312+0000` |
| `yyyy-MM-dd HH:mm:ss.SSS Z` | `2022-07-27 22:46:30.312 +0000` |
| `dd MMM yyyy` | `27 Jul 2022` |
| `yyyy-MM-dd HH:mm:ssZ` | `2022-07-27 22:46:30+0000` |
| `EEE MMM dd HH:mm:ss yyyy` | `Wed Jul 27 22:46:30 2022` |
| `yyyy-MM-ddTHH:mm:ss.SSS` | `2022-07-27T22:46:30.312` |
| `yyyy-MM-ddHH:mm:ss` | `2022-07-2722:46:30` |
| `MM/dd/yyyy HH:mm:ss A` | `07/27/2022 10:46:30 PM` |
| `yyyy MMM d HH:mm:ss Z` | `2022 Jul 27 22:46:30 +0000` |
| `MMM d HH:mm:ss yyyy` | `Jul 27 22:46:30 2022` |
| `yyyy-MM-dd HH:mm:ss.SSSZ` | `2022-07-27 22:46:30.312+0000` |
| `MMM  d yyyy HH:mm:ss` | `Jul  27 2022 22:46:30` |
| `yyyy-mm-ddTHH:mm:ss.SSSZ` | (variant) |
| `yyyy-MMM-dd HH:mm:ss` | `2022-Jul-27 22:46:30` |
| `yyyy-MM-ddTHH:mm:ss.SSSSSSSSSZ` | Nanosecond precision |
| `MM-dd-yyyy HH:mm:ss` | `07-27-2022 22:46:30` |
| `yy-MM-dd HH:mm:ss` | `22-07-27 22:46:30` |
| `yyyy-MM-dd HH:mm:ss.S` | `2022-07-27 22:46:30.3` |
| `MMM dd yyyy HH:mm:ss` | `Jul 27 2022 22:46:30` |

#### System-Supplied Timestamps

| Timestamp | Description |
|---|---|
| `@createTimestamp` | Always included. Represents the time Google SecOps received the logs. |
| `@timestamp` | Optional. The timestamp provided by Splunk or PCAP collection, if it exists. |
| `@collectionTimestamp` | Optional value at the log entry level. Represents the time the Forwarder collected the log entry. May not be present for logs ingested using the out-of-band processor. |

> **Important:** At the end of processing, Google SecOps uses the timestamp present in the `@timestamp` field as the timestamp value for all events. By default, the `date` filter takes precedence to populate the `@timestamp` field. If there is a need to use the log receipt time as the event timestamp, you can use the `rename` function to rename `@createTimestamp` to `@timestamp`. Best practice is to use the log message and extract the date value. If the log doesn't include a timestamp value, you might need to use `@createTimestamp` for log ingestion.

> **Important:** For `UNIX` and `UNIX_MS` date formats, use the `on_error` statement to handle errors.

#### Date Function Syntax

```
date {
  match => ["token", "format"]
  on_error => "no_match"
}
```

#### Date Function Examples

**Basic date parsing:**

```
date {
  match => ["when", "yyyy-MM-dd HH:mm:ss"]
  on_error => "no_match"
}
```

**Date with timezone specification:**

```
date {
  match => ["logtime", "yyyy-MM-dd HH:mm:ss"]
  timezone => "America/New_York"
  on_error => "no_match"
}
```

**Multiple date formats:**

```
date {
  match => ["ts", "yyyy-MM-dd HH:mm:ss", "UNIX", "ISO8601", "UNIX_MS"]
  on_error => "no_match"
}
```

#### Handle Timestamps Without a Year Value — Rebase Option

The `rebase` option for the date filter supports the ability to handle timestamps without a year value. It sets the year based on the time the data was ingested.

```
date {
  match => ["when", "MMM dd HH:mm:ss"]
  rebase => true
  on_error => "no_match"
}
```

#### Extract a Timestamp From a Raw Log

You can extract a timestamp from a raw log and store the value to a UDM field, such as `metadata.collected_timestamp`.

**Example — extract ISO 8601 timestamp from a 1Password raw log:**

Given a raw log with a `timestamp` field:

```json
{
  "country": "US",
  "target_user": {
    "uuid": "FTASPXQHWRF3XMJDLGKWBMZ2LI",
    "name": "Stephanie Badum",
    "email": "abc.def.@demo.com"
  },
  "location": {
    "country": "US",
    "region": "California",
    "city": "Hawthorne",
    "latitude": 33.9168,
    "longitude": -118.3432
  },
  "category": "success",
  "type": "mfa_ok",
  "details": null,
  "client": {
    "os_name": "Windows",
    "os_version": "10.0",
    "ip_address": "2603:8000:7600:c4e1:4db:400b:ff2:6626",
    "app_name": "1Password Browser Extension",
    "app_version": "20216",
    "platform_name": "Chrome",
    "platform_version": "89.0.4389.82"
  },
  "uuid": "EPNGUJLHFVHCXMJL5LJQGXTENA",
  "session_uuid": "UYA65VLTKZAMJAYVODY6BJ36VE",
  "timestamp": "2022-07-27T22:46:30.312374636Z"
}
```

**Parser code:**

```
filter {
  json {
    source => "message"
    on_error => "json_failure
    array_function => "split_columns"
  }

  grok {
    match => { "timestamp" => "%{TIMESTAMP_ISO8601:EventTime}" }
    on_error => "time_stamp_failure"
  }

  if [EventTime] != "" {
    date {
      match => ["EventTime", "ISO8601"]
      target => "event.idm.read_only_udm.metadata.collected_timestamp"
    }
  }
}
```

> **Note:** If the `target` field is not specified in the `date` function, the timestamp is mapped to the `metadata.event_timestamp` UDM field.

### 12.3 Drop Function

The `drop` function is used to drop all messages that reach this filter logic.

**Syntax:**

```
drop {
  tag => "TAG_MALFORMED_MESSAGE"
}
```

**Example:**

```
if [domain] == "-" {
  drop {
    tag => "TAG_MALFORMED_MESSAGE"
  }
}
```

---

## 13. Conditional Logic

Conditionals are consistent with the Logstash documentation concerning the usage of conditional statements. With parser syntax, only use conditionals as part of the filter logic for event transformation. The available conditional logic statements are `if`, `if/else`, and `if/else if/else`.

### 13.1 Conditional Syntax — If

```
if [token] == "value" {
  <code block>
}
```

### 13.2 Conditional Syntax — If/Else

```
if [token1] == "value1" and [token2] == "value2" {
  <code block 1>
} else {
  <code block 2>
}
```

### 13.3 Conditional Syntax — If/Else If/Else

```
if [token] == "value1" {
  <code block 1>
} else if [token] == "value2" {
  <code block 2>
} else {
  <code block 3>
}
```

### 13.4 Conditional Examples

**Using `in` operator:**

```
if [protocol] in ["tcp", "udp", "icmp"] {
  mutate {
    uppercase => [ "protocol" ]
  }
}
```

**If/else if/else with multiple conditions:**

```
if [action] == "drop" or [action] == "deny" or [action] == "drop ICMP" {
  mutate {
    replace => {
      "event.idm.read_only_udm.security_result.action" => "BLOCK"
    }
  }
} else if [action] == "allow" {
  mutate {
    replace => {
      "event.idm.read_only_udm.security_result.action" => "ALLOW"
    }
  }
} else {
  mutate {
    replace => {
      "event.idm.read_only_udm.security_result.action" => "UNKNOWN_ACTION"
    }
  }
}
```

---

## 14. Error Handling — on_error

Set the `on_error` property on any filter to catch errors. This property sets a value to `true` if an error was encountered, and `false` otherwise.

**Syntax:**

```
on_error => "<value>"
```

**Example — check if a value is an IP address:**

```
mutate {
  convert => { "host" => "ipaddress" }
  on_error => "is_not_ip"
}

if [is_not_ip] {
  # This means it's not an IP
}
```

---

## 15. Output Data to a UDM Record

Use the `merge` function to generate output. It is possible to generate more than one event message based on a single log line.

### 15.1 Generating Output — Single Event

```
mutate {
  merge => { "@output" => "event" }
}
```

### 15.2 Generating Output — Multiple Events

```
if [event1] != "" {
  mutate {
    merge => { "@output" => "event1" }
  }
}

if [event2] != "" {
  mutate {
    merge => { "@output" => "event2" }
  }
}
```

> **Important:** When generating multiple events as output, instead of assigning field values to `event.*`, use a designation such as `event1.*` and `event2.*` to differentiate between the values assigned to the first event versus the second event.

---

## 16. UDM Field Mapping

Parser fields are mapped to Chronicle UDM fields using the `event.idm.read_only_udm.*` namespace.

### Common UDM Mapping Examples

| Extracted Token | UDM Field Path |
|---|---|
| Source IP | `event.idm.read_only_udm.principal.ip` |
| Destination IP | `event.idm.read_only_udm.target.ip` |
| Source Hostname | `event.idm.read_only_udm.principal.hostname` |
| Destination Hostname | `event.idm.read_only_udm.target.hostname` |
| IP Protocol | `event.idm.read_only_udm.network.ip_protocol` |
| Target Port | `event.idm.read_only_udm.network.target.port` |
| Security Action | `event.idm.read_only_udm.security_result.action` |
| Event Type | `event.idm.read_only_udm.metadata.event_type` |
| Event Timestamp | `event.idm.read_only_udm.metadata.event_timestamp` |
| Collected Timestamp | `event.idm.read_only_udm.metadata.collected_timestamp` |
| Labels | `event.idm.read_only_udm.principal.resource.attribute.labels` |
| Observer IP | `event.idm.read_only_udm.observer.ip` |

### Mapping Methods

| Method | Use Case |
|---|---|
| `rename` | When the extracted token can be directly assigned (same data type). |
| `replace` | When assigning constants or formatted string values. |
| `merge` | When assigning to repeated fields (e.g., IP addresses) or generating output. |
| `convert` + `rename` | When the extracted value needs type conversion before mapping. |

---

## 17. Validate Data Using the Statedump Plugin

Use the `statedump` filter plugin to validate the internal state of a parser. The filter shows all the values set by the parser during troubleshooting. You can use multiple blocks of statedump filters using the `label` property.

> **Important:** You can use the `statedump` filter for **troubleshooting only**. You **must remove** the statedump filter blocks before validation.

**Syntax:**

```
statedump {
  label => "foo"
}
```

**Statedump output example:**

```json
Internal State (label=foo):
{
  "@createTimestamp": { "nanos": 0, "seconds": 1693549534 },
  "@enableCbnForLoop": true,
  "@onErrorCount": 0,
  "@output": [],
  "@timezone": "",
  "event": {
    "idm": {
      "read_only_udm": {
        "metadata": {
          "event_type": "GENERIC_EVENT"
        }
      }
    }
  },
  "message": "my sample log"
}
```

### Statedump Output Fields

| Field | Description |
|---|---|
| `@createTimestamp` | The time when this dump was created. |
| `@enableCbnForLoop` | Internal flag. |
| `@onErrorCount` | Number of errors discovered so far. |
| `@output` | The final output from the parser. |
| `@timezone` | The offset from UTC for log entries. |
| `event` | The event mapping done by the parser. |
| `message` | The log message which the parser is run against. |

---

## 18. Best Practices

1. **Use the log message timestamp** — Best practice is to extract the date value from the log message itself. If the log doesn't include a timestamp, use `@createTimestamp` for log ingestion.
2. **Initialize tokens before use** — Initialize tokens to an empty string `""` before using them in `if` statements or `replace` operations, especially after JSON extraction.
3. **Use `on_error` extensively** — Set `on_error` on every filter operation to gracefully handle parsing failures without dropping the entire log.
4. **Use separate mutate blocks** — When order of operations matters, break transformations into separate `mutate` blocks rather than combining them.
5. **Remove statedump before validation** — The `statedump` filter is for troubleshooting only and must be removed before parser validation.
6. **Use `merge` for repeated fields** — When mapping to repeated UDM fields (like IP addresses), use the `merge` function.
7. **Convert data types appropriately** — Use `convert` to ensure data types match the target UDM field schema before assignment.
8. **Handle non-existing JSON elements carefully** — Follow the rules for accessing non-existing elements (use `merge` or `rename` which silently ignore missing elements; avoid `replace` which causes errors).

---

## 19. Important Notes and Constraints

- **Parser syntax is similar to Logstash but not identical.** Do not assume full Logstash compatibility.
- **Conditional logic is limited** to `if`, `if/else`, and `if/else if/else` — only within the `filter` block for event transformation.
- **The `replace` function can only be used for string values.**
- **The `rename` function** requires the original token and new token to be of the same data type.
- **Non-existing JSON elements** cannot be accessed via `if` statements unless initialized first.
- **Non-existing JSON elements** cannot be used in `replace` (causes error) but can be used in `rename` and `merge` (silently ignored).
- **XML iteration index starts at 1**, while JSON array index starts at **0**.
- **The `gsub` function** only applies to string fields.
- **To search for a literal backslash** in `gsub`, use four backslashes (`\\\\`).
- **Mutate operations in a single block** execute in the order defined by the Logstash mutate plugin documentation, not the order written.
- **The `date` filter** populates `@timestamp` by default. If `target` is specified, the parsed date is stored in that UDM field instead.
- **If `target` is not specified** in the `date` function, the timestamp maps to `metadata.event_timestamp`.
- **For `UNIX` and `UNIX_MS`** date formats, always use `on_error` to handle errors.
- **Multiple date formats** can be specified in a single `date` filter — they are tried in order.
- **The `rebase` option** sets the year based on ingestion time for timestamps without a year value.
- **The `copy` function** performs a deep copy — changes to the destination do not affect the source.
- **The `drop` function** discards the entire log message from further processing.
- **The `statedump` filter** must be removed before parser validation.
- **Both `key` and `value`** in the `map` loop are of type `string`.

---

## 20. Complete Parser Example

The following is a comprehensive example demonstrating a complete parser flow:

```
filter {
  # Step 1: Extract data using Grok
  grok {
    match => {
      "message" => "%{SYSLOGTIMESTAMP:when} %{DATA:deviceName}: FW-%{INT:messageid}: (?P<action>Accepted|Denied) connection %{WORD:protocol} %{IP:srcAddr}/%{INT:srcPort} to %{IP:dstAddr}/%{INT:dstPort}"
    }
    on_error => "grok_failure"
  }

  # Step 2: Parse the date
  date {
    match => ["when", "MMM dd HH:mm:ss"]
    rebase => true
    on_error => "no_match"
  }

  # Step 3: Transform protocol to uppercase
  mutate {
    lowercase => [ "protocol" ]
  }

  if [protocol] in ["tcp", "udp", "icmp"] {
    mutate {
      uppercase => [ "protocol" ]
    }
  }

  # Step 4: Map action to UDM security result
  if [action] == "Accepted" {
    mutate {
      replace => {
        "event.idm.read_only_udm.security_result.action" => "ALLOW"
      }
    }
  } else if [action] == "Denied" {
    mutate {
      replace => {
        "event.idm.read_only_udm.security_result.action" => "BLOCK"
      }
    }
  }

  # Step 5: Rename fields to UDM paths
  mutate {
    rename => {
      "protocol" => "event.idm.read_only_udm.network.ip_protocol"
    }
  }

  # Step 6: Map IP addresses using merge (repeated fields)
  mutate {
    merge => { "event.idm.read_only_udm.principal.ip" => "srcAddr" }
  }
  mutate {
    merge => { "event.idm.read_only_udm.target.ip" => "dstAddr" }
  }

  # Step 7: Map hostname
  mutate {
    replace => {
      "event.idm.read_only_udm.principal.hostname" => "%{deviceName}"
    }
  }

  # Step 8: Set event type
  mutate {
    replace => {
      "event.idm.read_only_udm.metadata.event_type" => "NETWORK_CONNECTION"
    }
  }

  # Step 9: Output
  mutate {
    merge => { "@output" => "event" }
  }
}
```

---

## 21. Quick Reference — Extraction Functions

| Function | Purpose | Source Format |
|---|---|---|
| `grok` | Extract fields using predefined patterns and regex | Syslog, unstructured text |
| `json` | Parse JSON-formatted logs | JSON |
| `xml` | Parse XML-formatted logs using XPath | XML |
| `kv` | Parse key-value pair formatted logs | Key-value strings |
| `csv` | Parse CSV-formatted logs | CSV |

## 22. Quick Reference — Mutate Operations

| Operation | Purpose |
|---|---|
| `convert` | Change data type of a field |
| `gsub` | Regex find-and-replace on string fields |
| `lowercase` | Convert field value to lowercase |
| `uppercase` | Convert field value to uppercase |
| `merge` | Join fields or output events |
| `rename` | Rename a token to a new field path |
| `replace` | Assign a constant or variable value to a token |
| `remove_field` | Destroy/delete a token |
| `copy` | Deep copy a token value |
| `split` | Split a string into an iterable array |

## 23. Quick Reference — Other Functions

| Function | Purpose |
|---|---|
| `base64` | Decode base64-encoded values |
| `date` | Parse and normalize timestamps |
| `drop` | Discard log messages |
| `statedump` | Debug/inspect parser internal state |

## 24. Quick Reference — Loop Constructs

| Construct | Syntax | Index Start |
|---|---|---|
| For loop (array) | `for <item> in <array> { ... }` | N/A |
| For loop with index (array) | `for index, <item> in <array> { ... }` | 0 |
| For loop (XML) | `for index, _ in xml(message, /xpath) { ... }` | 1 |
| Map loop (object key-value) | `for key, value in <object> map { ... }` | N/A |

---

