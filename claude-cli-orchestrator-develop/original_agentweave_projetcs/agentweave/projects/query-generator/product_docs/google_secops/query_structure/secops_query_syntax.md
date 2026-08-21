# YARA-L Search Query Syntax

> **Purpose:** Complete reference for building YARA-L dashboard and search queries in Google Chronicle SecOps

## 1. YARA-L Dashboard Query Syntax

YARA-L is a **section-based**, declarative language — unlike SPL's pipe-chained commands. A dashboard query is organized into distinct named sections: [[YARA-L Getting Started](https://docs.cloud.google.com/chronicle/docs/yara-l/getting-started#yaral-structure)]

| Section | Required in Dashboards? | Purpose |
|---|---|---|
| `events` | Yes | Filter/define data sources |
| `match` | Optional | Group results |
| `outcome` | Optional | Aggregate/calculate |
| `dedup` | Optional | Remove duplicates |
| `order` | Optional | Sort results |
| `limit` | Optional | Restrict result count |
| `select` / `unselect` | Optional | Include/exclude UDM fields |

## 2. Filtering and Search Patterns

The `events` section replaces SPL's `search` command. You define UDM field conditions directly: [[SPL to YARA-L](https://docs.cloud.google.com/chronicle/docs/yara-l/transition_spl_yaral#commands-spl-yaral)]

```yara-l
metadata.event_type = "USER_LOGIN"
security_result.action = "FAIL"
principal.user.userid != ""
```

Boolean logic is supported:

```yara-l
principal.hostname != "" AND metadata.event_type = "NETWORK_CONNECTION"
```

## 3. Match Section Usage in Dashboards

The `match` section groups events by one or more fields. In dashboards, use the `by` operator with a time unit (tumbling window): [[Time Windowing](https://docs.cloud.google.com/chronicle/docs/yara-l/transition_spl_yaral#time-windowing)]

```yara-l
metadata.event_type = "USER_LOGIN"
$user = principal.user.userid

match:
  $user by day
```

Supported time units include `minute`, `hour`, `day`, `week`. The `by` keyword creates fixed, non-overlapping **tumbling windows**, ideal for dashboards and search aggregations.

## 4. Outcome Section for Aggregations

The `outcome` section calculates metrics over grouped events: [[eval-outcome](https://docs.cloud.google.com/chronicle/docs/yara-l/transition_spl_yaral#eval-outcome)]

```yara-l
metadata.event_type = "USER_LOGIN"
security_result.action = "FAIL"
$user = principal.user.userid

match:
  $user by day

outcome:
  $daily_failed_login_count = count(metadata.id)
```

## 5. Available Aggregation Functions

[[Aggregation Functions](https://docs.cloud.google.com/chronicle/docs/investigation/statistics-aggregations-in-udm-search#aggregate-functions); [Aggregation Queries](https://docs.cloud.google.com/chronicle/docs/yara-l/transition_spl_yaral#aggregation-stat-queries)]

| Function | Description | Example |
|---|---|---|
| `count()` | Count total events | `$count = count(metadata.id)` |
| `count_distinct()` | Count unique values | `$unique_users = count_distinct(principal.user.userid)` |
| `sum()` | Sum a numeric field | `$bytes = sum(network.sent_bytes)` |
| `avg()` | Average of a field | `$avg_bytes = avg(network.sent_bytes)` |
| `min()` / `max()` | Min or max value | `$range = max(network.sent_bytes) - min(network.sent_bytes)` |
| `array()` | List of values (max 25) | `$event_types = array(metadata.event_type)` |
| `array_distinct()` | List of distinct values | `$distinct_types = array_distinct(metadata.event_type)` |
| `window.median` | Median value | `$median = window.median(target.file.size, false)` |
| `earliest()` | Earliest timestamp | `$start = earliest(metadata.event_timestamp)` |

## 6. Order and Limit Clauses

Use `order` to sort and `limit` to restrict the number of results: [[YARA-L Structure](https://docs.cloud.google.com/chronicle/docs/yara-l/transition_spl_yaral#queries-rules)]

```yara-l
metadata.log_type = "OKTA"

match:
  principal.ip

outcome:
  $user_count_by_ip = count(principal.user.userid)

order:
  $user_count_by_ip desc

limit:
  20
```

> **Note:** `order` and `limit` are only applicable when `match` is used, and are **not available in Rules** — only in Search and Dashboards.

## 7. UDM Field Access in Dashboard Queries

In dashboard/search queries, you access UDM fields directly (the `$e.` event variable prefix is optional): [[UDM Field List](https://docs.cloud.google.com/chronicle/docs/reference/udm-field-list#udm-field-list)]

```yara-l
// Both forms are valid in dashboards:
principal.hostname != ""
$e.principal.hostname != ""
```

Common UDM field patterns:
- `metadata.event_type` — event classification
- `principal.user.userid` — acting user
- `target.ip` — destination IP
- `network.sent_bytes` — bytes transferred
- `security_result.action` — ALLOW/BLOCK/FAIL

## 8. String Matching and Regex Patterns

**Exact match:**
```yara-l
metadata.event_type = "USER_LOGIN"
```

**Regex match (inline):**
```yara-l
$e.principal.hostname = /google/
```

**Regex via function:** [[re.regex](https://docs.cloud.google.com/chronicle/docs/yara-l/functions#reregex)]
```yara-l
re.regex($e.principal.hostname, "google")
```

**Capture groups:** [[re.capture](https://docs.cloud.google.com/chronicle/docs/yara-l/functions#recapture)]
```yara-l
// Extract domain from email
"google.com" = re.capture($e.network.email.from, "@(.*)")
```

**Replace:** [[re.replace](https://docs.cloud.google.com/chronicle/docs/yara-l/functions#rereplace)]
```yara-l
"email@google.org" = re.replace($e.network.email.from, "com", "org")
```

## 9. Best Practices for Dashboard Queries

- **Always start with `events`** — it is required even in dashboards. [[YARA-L Getting Started](https://docs.cloud.google.com/chronicle/docs/yara-l/getting-started#build-first-search)]
- **Use placeholder variables** (`$user`, `$host`) to bridge grouping between `events` and `match`.
- **Use `by` (tumbling windows)** in dashboards rather than `over` (sliding/hop windows, which are for rules).
- **Statistical queries are available 2 hours after ingestion** — plan dashboard refresh cycles accordingly. [[Aggregations](https://docs.cloud.google.com/chronicle/docs/investigation/statistics-aggregations-in-udm-search)]
- **Use `dedup`** before aggregation to reduce duplicate noise.
- **Use the UDM Lookup tool** in Google SecOps UI to verify field names before building queries. [[Tools](https://docs.cloud.google.com/chronicle/docs/yara-l/getting-started#tools-build-query)]

## 10. Common Visualization Patterns

**Detection severity over time:** [[Dashboard Widget](https://docs.cloud.google.com/chronicle/docs/yara-l/getting-started#create-widget)]
```yara-l
detection.detection.severity != "UNKNOWN_SEVERITY"
$severity = detection.detection.severity

match:
  $severity by hour

outcome:
  $detection_count = count_distinct(detection.id)
```

**Top IPs by event count:**
```yara-l
$ip = group(principal.ip, about.ip, target.ip)
$ip != ""

match:
  $ip

outcome:
  $count = count_distinct(metadata.id)

order:
  $count desc
```
[[group function](https://docs.cloud.google.com/chronicle/docs/yara-l/functions#group)]

**User login frequency:**
```yara-l
events:
  metadata.event_type = "USER_LOGIN"

match:
  target.user.userid

outcome:
  $login_count = count(metadata.id)
```
