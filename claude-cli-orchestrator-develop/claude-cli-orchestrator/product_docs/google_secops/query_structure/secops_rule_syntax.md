# YARA-L Detection Rule Syntax

> **Purpose:** Complete reference for building YARA-L 2.0 detection rules in Google Chronicle SecOps

## 1. YARA-L 2.0 Rule Structure

YARA-L is a **declarative, section-based** language (unlike SPL's procedural pipe syntax). Every rule is wrapped in a named `rule` block: [[YARA-L Getting Started](https://docs.cloud.google.com/chronicle/docs/yara-l/getting-started#build-rule)]

```yara-l
rule RuleName {
  meta:    ...
  events:  ...
  match:   ...
  outcome: ...
  condition: ...
}
```

## 2. Available Sections

| Section | Rules | Search/Dashboards | Purpose |
|---|---|---|---|
| `meta` | Required | Optional | Descriptive metadata (author, description, severity) |
| `events` | Required | Required | Define and filter events using UDM fields |
| `match` | Required (multi-event) | Optional | Group by fields; specify time window |
| `outcome` | Optional | Optional | Calculate metrics, aggregations |
| `condition` | Required | Optional | Trigger logic / threshold |
| `dedup` | N/A | Optional | Remove duplicates |
| `order` | N/A | Optional | Sort results |
| `limit` | N/A | Optional | Cap result count |

[[YARA-L Structure](https://docs.cloud.google.com/chronicle/docs/yara-l/getting-started#yaral-structure)]

## 3. Event Variable Syntax

Event variables (e.g., `$e`, `$e1`, `$e2`) act as **logical groupings of filters** representing specific events: [[Rule Structure Syntax](https://docs.cloud.google.com/chronicle/docs/yara-l/getting-started#rule-structure-syntax)]

```yara-l
events:
  $e.metadata.event_type = "USER_LOGIN"
  $e.security_result.action = "FAIL"
  $e.principal.user.userid != ""
  $userid = $e.principal.user.userid
```

For **multi-event rules**, use multiple event variables and join them via a shared placeholder:

```yara-l
events:
  $e1.principal.ip = "1.1.1.1"
  $e1.metadata.event_type = "PROCESS_LAUNCH"
  $e2.target.file.sha256 = "badhash..."
  $user = $e1.principal.user.userid
  $user = $e2.principal.user.userid
```
[[Multi-event Rule](https://docs.cloud.google.com/chronicle/docs/yara-l/getting-started#build-multievent-rule)]

## 4. UDM Field Access Patterns

**In Rules (Detect Engine):** prefix with `$event` or event variable:
- `$e.metadata.event_type`
- `$e.principal.user.userid`
- `$e.target.file.sha256`

**In Parsers (CBN):** prefix with `event.idm.read_only_udm`:
- `event.idm.read_only_udm.metadata.event_type`
- `event.idm.read_only_udm.principal.user.userid`

[[UDM Field List](https://docs.cloud.google.com/chronicle/docs/reference/udm-field-list#udm-field-list)]

## 5. Available Functions and Operators

Key functions available in YARA-L:

| Function | Purpose |
|---|---|
| `re.regex($e.field, "pattern")` | Regex matching |
| `net.ip_in_range_cidr(ip, "cidr")` | IP range check |
| `strings.concat(...)` | String concatenation |
| `strings.to_lower/upper()` | Case conversion |
| `cast.as_int() / cast.as_bool()` | Type casting |
| `math.abs/ceil/floor/log/pow/sqrt()` | Math operations |
| `timestamp.now()` | Current timestamp |
| `arrays.concat(arr1, arr2)` | Array concatenation |

[[YARA-L Functions](https://docs.cloud.google.com/chronicle/docs/yara-l/functions)]

**Regex example:**
```yara-l
re.regex($e.principal.hostname, "google")
// Equivalent to: $e.principal.hostname = /google/
```
[[re.regex](https://docs.cloud.google.com/chronicle/docs/yara-l/functions#reregex)]

## 6. Time Window Specifications

Two types of time windows are used in the `match` section: [[Time Windowing](https://docs.cloud.google.com/chronicle/docs/yara-l/transition_spl_yaral#time-windowing)]

| Type | Operator | Use Case |
|---|---|---|
| **Sliding** (hop) | `over` | Rules -- continuous, rolling detection |
| **Tumbling** | `by` | Search/Dashboards -- fixed intervals (e.g., by day/hour) |

```yara-l
match:
  $userid over 5m   // Sliding -- for rules

match:
  $userid by day    // Tumbling -- for search/dashboards
```

## 7. Aggregation Functions in Outcome

[[Aggregate Functions](https://docs.cloud.google.com/chronicle/docs/investigation/statistics-aggregations-in-udm-search#aggregate-functions)]

| Function | Description |
|---|---|
| `count(expr)` | Count of rows in a group |
| `count_distinct(expr)` | Count of distinct values |
| `sum(expr)` | Sum of numeric field |
| `avg(expr)` | Average of numeric field |
| `min(expr)` / `max(expr)` | Min or max value |
| `array(expr)` | All values as a list (max 25) |
| `array_distinct(expr)` | Distinct values as a list |
| `earliest(timestamp)` | Earliest timestamp |
| `window.median(field, asc)` | Median value |
| `window.first/last(val, sort_by)` | First/last value by sort key |
| `window.stddev(field)` | Standard deviation |

## 8. Condition Section Syntax and Examples

The `condition` section defines the threshold to trigger an alert: [[Rule Structure Syntax](https://docs.cloud.google.com/chronicle/docs/yara-l/getting-started#rule-structure-syntax)]

```yara-l
condition:
  #e > 5                // Event count must exceed 5
  $e                    // At least one matching event exists
  $e1 or $e2            // Either event must exist (multi-event)
  $failed_count > 5     // Outcome variable threshold
```

## 9. Complete Detection Rule Examples

### Brute Force Detection (Sliding Window)
[[Sliding Window Example](https://docs.cloud.google.com/chronicle/docs/yara-l/transition_spl_yaral#sliding-time-window)]
```yara-l
rule TooManyFailedLogins {
  meta:
    author = "Security Team"
    description = "Detects >5 failed logins within 5 minutes."
    severity = "Medium"

  events:
    $e.metadata.event_type = "USER_LOGIN"
    $e.security_result.action = "FAIL"
    $e.principal.user.userid != ""
    $userid = $e.principal.user.userid

  match:
    $userid over 5m

  outcome:
    $failed_count = count($e.metadata.id)

  condition:
    #e > 5
}
```

### Multi-Event Correlation
[[Multi-event Rule](https://docs.cloud.google.com/chronicle/docs/yara-l/getting-started#build-multievent-rule)]
```yara-l
rule MultiEventExample {
  meta:
    author = "Security Team"
    description = "Detects bad hash execution or process launch from specific IP."

  events:
    $e1.principal.ip = "1.1.1.1"
    $e1.metadata.event_type = "PROCESS_LAUNCH"
    $e2.target.file.sha256 = "badhash..."
    $user = $e1.principal.user.userid
    $user = $e2.principal.user.userid

  match:
    $user over 5m

  condition:
    $e1 or $e2
}
```

### Specific File Hash Detection
[[File Hash Rule](https://docs.cloud.google.com/chronicle/docs/yara-l/transition_spl_yaral#create-rule-alert)]
```yara-l
rule specific_file_hash_detected {
  meta:
    rule_name = "Specific File Hash Detected"
    description = "Detects events where a specific file hash is present."
    severity = "Medium"

  events:
    $e.target.file.sha256 = "hash67890"

  outcome:
    $time = array_distinct($e.metadata.event_timestamp.seconds)
    $file_hashes = array_distinct($e.target.file.sha256)

  condition:
    $e
}
```

## 10. Best Practices

- **Use event variables** (`$e`) as prefixes for all fields in the `events` section of rules.
- **Placeholder variables** (e.g., `$userid`) bridge multiple event streams and group `match` results.
- **Set thresholds** in `condition` based on your security requirements and detection objectives.
