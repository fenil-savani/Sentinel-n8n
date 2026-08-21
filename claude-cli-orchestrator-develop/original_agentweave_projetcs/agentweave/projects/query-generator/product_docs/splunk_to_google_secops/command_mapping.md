# Splunk → Chronicle SecOps / YARA-L Command Mapping

> **Purpose:** Command and function migration reference for Splunk to Google Chronicle SecOps (YARA-L 2.0)

---

## Quick Reference Matrix

| Category | Splunk Function | YARA-L Function | Dashboard | Search | Rule | Workaround |
|----------|----------------|-----------------|-----------|--------|------|------------|
| Comparison | false() | - | Yes | Yes | Yes | Yes |
| Comparison | in() | - | No | No | No | No |
| Comparison | lookup() | - | Yes | Yes | Yes | No |
| Comparison | null() | - | Yes | Yes | Yes | Yes |
| Comparison | nullif() | - | Yes | Yes | Yes | Yes |
| Comparison | searchmatch() | - | No | No | No | No |
| Comparison | true() | - | Yes | Yes | Yes | Yes |
| Comparison | validate() | - | Yes | Yes | Yes | Yes |
| Comparison | cidrmatch() | net.ip_in_range_cidr() | Yes | Yes | Yes | No |
| Comparison | coalesce() | strings.coalesce() | Yes | Yes | Yes | No |
| Comparison | case() | nested if | Yes | Yes | Yes | Yes |
| Comparison | match() | re.regex() | Yes | Yes | Yes | No |
| Comparison | like() | re.regex() | Yes | Yes | Yes | No |
| Conversion | ipmask() | - | No | No | No | No |
| Conversion | printf() | - | No | No | No | No |
| Conversion | toarray() | - | No | No | No | No |
| Conversion | todouble() | - | No | No | No | No |
| Conversion | toint() | - | No | No | No | No |
| Conversion | tonumber() | - | No | No | No | No |
| Conversion | tomv() | - | No | No | No | No |
| Conversion | toobject() | - | No | No | No | No |
| Conversion | tobool() | cast.as_bool() | Yes | Yes | Yes | No |
| Conversion | tostring() | cast.as_string() | Yes | Yes | Yes | No |
| Cryptographic | md5() | - | No | No | No | No |
| Cryptographic | sha1() | - | No | No | No | No |
| Cryptographic | sha256() | hash.sha256() | Yes | Yes | Yes | No |
| Cryptographic | sha512() | - | No | No | No | No |
| Date/Time | relative_time() | - | No | No | No | No |
| Date/Time | strftime() | timestamp.get_timestamp | - | - | - | No |
| Date/Time | strptime() | timestamp.as_unix_seconds | - | - | - | No |
| Date/Time | now() | timestamp.now() | Yes | Yes | Yes | No |
| Mathematical | exp() | - | No | No | No | No |
| Mathematical | exact() | - | No | No | No | No |
| Mathematical | log() | - | No | No | No | No |
| Mathematical | sigfig() | - | No | No | No | No |
| Mathematical | round() | math.round() | Yes | Yes | Yes | No |
| Mathematical | abs() | math.abs() | Yes | Yes | Yes | No |
| Mathematical | ceil() | math.ceil() | Yes | Yes | Yes | No |
| Mathematical | floor() | math.floor() | Yes | Yes | Yes | No |
| Mathematical | ln() | math.log() | Yes | Yes | Yes | No |
| Mathematical | pow() | math.pow() | Yes | Yes | Yes | No |
| Mathematical | sqrt() | math.sqrt() | Yes | Yes | Yes | No |
| Mathematical | sum() | sum() | Yes | Yes | Yes | No |
| Mathematical | pi() | - | Yes | Yes | Yes | Yes |
| Multivalue | commands() | - | No | No | No | No |
| Multivalue | mvappend() | - | No | No | No | No |
| Multivalue | mvcount() | - | No | No | No | No |
| Multivalue | mvfilter() | - | No | No | No | No |
| Multivalue | mvfind() | - | No | No | No | No |
| Multivalue | mvindex() | - | No | No | No | No |
| Multivalue | mvmap() | - | No | No | No | No |
| Multivalue | mvrange() | - | No | No | No | No |
| Multivalue | mvreverse() | - | No | No | No | No |
| Multivalue | mvsort() | - | No | No | No | No |
| Multivalue | mvzip() | - | No | No | No | No |
| Multivalue | mv_to_json_array() | - | No | No | No | No |
| Multivalue | mvjoin() | arrays.join_string() | Yes | Yes | Yes | No |
| Multivalue | split() | strings.split() | Yes | Yes | Yes | No |
| Multivalue | mvdedup() | array_distinct() | Yes | Yes | Yes | No |
| Statistical | avg() | avg() | Yes | Yes | Yes | No |
| Statistical | min() | min() | Yes | Yes | Yes | No |
| Statistical | max() | max() | Yes | Yes | Yes | No |
| Statistical | random() | math.random() | Yes | Yes | Yes | No |
| Text | len() | array.length() | Yes | Yes | Yes | No |
| Text | spath() | - | No | No | No | No |
| Text | substr() | - | No | No | No | No |
| Text | lower() | strings.to_lower() | Yes | Yes | Yes | No |
| Text | ltrim() | strings.ltrim() | Yes | Yes | Yes | No |
| Text | replace() | re.replace() | Yes | Yes | Yes | No |
| Text | rtrim() | strings.rtrim() | Yes | Yes | Yes | No |
| Text | trim() | strings.trim() | Yes | Yes | Yes | No |
| Text | upper() | strings.to_upper() | Yes | Yes | Yes | No |
| Text | urldecode() | strings.url_decode() | Yes | Yes | Yes | No |
| Stats Aggregate | estdc | - | No | No | No | No |
| Stats Aggregate | estdc_error | - | No | No | No | No |
| Stats Aggregate | exactperc() | - | No | No | No | No |
| Stats Aggregate | perc | - | No | No | No | No |
| Stats Aggregate | stdevp | - | No | No | No | No |
| Stats Aggregate | sumsq | - | No | No | No | No |
| Stats Aggregate | upperperc() | - | No | No | No | No |
| Stats Aggregate | varp() | - | No | No | No | No |
| Stats Aggregate | count() | count() | Yes | Yes | Yes | No |
| Stats Aggregate | distinct_count() | distinct_count() | Yes | Yes | Yes | No |
| Stats Aggregate | max() | max() | Yes | Yes | Yes | No |
| Stats Aggregate | min() | min() | Yes | Yes | Yes | No |
| Stats Aggregate | sum() | sum() | Yes | Yes | Yes | No |
| Stats Aggregate | median() | window.median() | No | No | Yes | Yes |
| Stats Aggregate | mode() | window.mode() | No | No | Yes | Yes |
| Stats Aggregate | range() | window.range() | No | Yes | Yes | Yes |
| Stats Aggregate | var() | window.variance | No | No | Yes | Yes |
| Stats Aggregate | mean() | avg() | Yes | Yes | Yes | No |
| Stats Aggregate | stdev() | window.stdev() | No | No | Yes | Yes |
| Event Order | first() | window.first() | Yes | Yes | Yes | Yes |
| Event Order | last() | window.last() | Yes | Yes | Yes | Yes |
| Time | earliest() | window.first() | Yes | Yes | Yes | No |
| Time | earliest_time() | - | - | - | - | No |
| Time | latest() | latest() | Yes | Yes | Yes | No |
| Time | latest_time() | - | - | - | - | No |
| Time | per_day() | - | - | - | - | No |
| Time | per_hour() | - | - | - | - | No |
| Time | per_minute() | - | - | - | - | No |
| Time | per_second() | - | - | - | - | No |
| Time | rate() | - | - | - | - | No |
| Time | rate_avg() | - | - | - | - | No |
| Time | rate_sum() | - | - | - | - | No |

**Legend:** Yes = Supported | No = Not Supported | - = Not Applicable or No Data

---

## Function Details

### Comparison & Boolean Functions

#### `false()`
- **YARA-L:** Not available
- **Support:** Dashboard: Yes | Search: Yes | Rule: Yes
- **Workaround:** Yes
- **Details:** Use `0` instead of this function

#### `in()`
- **YARA-L:** Not available
- **Support:** Dashboard: No | Search: No | Rule: No
- **Workaround:** No

#### `lookup()`
- **YARA-L:** Not available
- **Support:** Dashboard: Yes | Search: Yes | Rule: Yes
- **Workaround:** No
- **Details:** Reference list

#### `null()`
- **YARA-L:** Not available
- **Support:** Dashboard: Yes | Search: Yes | Rule: Yes
- **Workaround:** Yes
- **Details:** Use empty string `""` instead of this function

#### `nullif()`
- **YARA-L:** Not available
- **Support:** Dashboard: Yes | Search: Yes | Rule: Yes
- **Workaround:** Yes
- **Details:** `if (field1=field2, "", field1)`

#### `searchmatch()`
- **YARA-L:** Not available
- **Support:** Dashboard: No | Search: No | Rule: No
- **Workaround:** No

#### `true()`
- **YARA-L:** Not available
- **Support:** Dashboard: Yes | Search: Yes | Rule: Yes
- **Workaround:** Yes
- **Details:** Use `1` instead of this function

#### `validate()`
- **YARA-L:** Not available
- **Support:** Dashboard: Yes | Search: Yes | Rule: Yes
- **Workaround:** Yes
- **Differences:** This function takes a list of conditions and values and returns the value that corresponds to the condition that evaluates to FALSE. This function defaults to NULL if all conditions evaluate to TRUE.
- **Example:**
  ```spl
  Splunk:
  | eval n=validate(port==3000, "ERROR: Port is not an integer", port <= 65535, "ERROR: Port is out of range")
  
  SecOps:
  case() - nested if
  outcome:
      $event_count = count_distinct($e.metadata.id)
      $eg = if($port!=3000.0, "ERROR: Port is not an integer", if($port>65535, "ERROR: Port is out of range"))
  
  NOTE: All the conditions will be reversed.
  ```

#### `cidrmatch()`
- **YARA-L:** `net.ip_in_range_cidr()`
- **Support:** Dashboard: Yes | Search: Yes | Rule: Yes
- **Workaround:** No

#### `coalesce()`
- **YARA-L:** `strings.coalesce()`
- **Support:** Dashboard: Yes | Search: Yes | Rule: Yes
- **Workaround:** No

#### `case()`
- **YARA-L:** nested if
- **Support:** Dashboard: Yes | Search: Yes | Rule: Yes
- **Workaround:** Yes
- **Example:**
  ```
  case() - nested if
  outcome:
      $event_count = count_distinct($e.metadata.id)
      $eg = if($event_count>1, "Big", if($event_count=1, "Medium"))
  ```

#### `match()`
- **YARA-L:** `re.regex()`
- **Support:** Dashboard: Yes | Search: Yes | Rule: Yes
- **Workaround:** No

#### `like()`
- **YARA-L:** `re.regex()`
- **Support:** Dashboard: Yes | Search: Yes | Rule: Yes
- **Workaround:** No

---

### Conversion Functions

#### `ipmask()`
- **YARA-L:** Not available
- **Support:** Dashboard: No | Search: No | Rule: No
- **Workaround:** No

#### `printf()`
- **YARA-L:** Not available
- **Support:** Dashboard: No | Search: No | Rule: No
- **Workaround:** No

#### `toarray()`
- **YARA-L:** Not available
- **Support:** Dashboard: No | Search: No | Rule: No
- **Workaround:** No

#### `todouble()`
- **YARA-L:** Not available
- **Support:** Dashboard: No | Search: No | Rule: No
- **Workaround:** No

#### `toint()`
- **YARA-L:** Not available
- **Support:** Dashboard: No | Search: No | Rule: No
- **Workaround:** No

#### `tonumber()`
- **YARA-L:** Not available
- **Support:** Dashboard: No | Search: No | Rule: No
- **Workaround:** No

#### `tomv()`
- **YARA-L:** Not available
- **Support:** Dashboard: No | Search: No | Rule: No
- **Workaround:** No

#### `toobject()`
- **YARA-L:** Not available
- **Support:** Dashboard: No | Search: No | Rule: No
- **Workaround:** No

#### `tobool()`
- **YARA-L:** `cast.as_bool()`
- **Support:** Dashboard: Yes | Search: Yes | Rule: Yes
- **Workaround:** No
- **Differences:** In Splunk `tobool()` function returns true for any non-zero integer, in SecOps `cast.as_bool()` function return true only for the integer 1.

#### `tostring()`
- **YARA-L:** `cast.as_string()`
- **Support:** Dashboard: Yes | Search: Yes | Rule: Yes
- **Workaround:** No
- **Differences:** Splunk `tostring(<value>,<format>)` takes different format like binary, hex, commas and duration. SecOps `cast.as_string(int_or_bytes_or_bool, optional_default_string)` does not support different formats instead it has functionality of default value.

---

### Cryptographic Functions

#### `md5()`
- **YARA-L:** Not available
- **Support:** Dashboard: No | Search: No | Rule: No
- **Workaround:** No

#### `sha1()`
- **YARA-L:** Not available
- **Support:** Dashboard: No | Search: No | Rule: No
- **Workaround:** No

#### `sha256()`
- **YARA-L:** `hash.sha256()`
- **Support:** Dashboard: Yes | Search: Yes | Rule: Yes
- **Workaround:** No

#### `sha512()`
- **YARA-L:** Not available
- **Support:** Dashboard: No | Search: No | Rule: No
- **Workaround:** No

---

### Date and Time Functions

#### `relative_time()`
- **YARA-L:** Not available
- **Support:** Dashboard: No | Search: No | Rule: No
- **Workaround:** No

#### `strftime()`
- **YARA-L:** `timestamp.get_timestamp`
- **Support:** Not specified
- **Workaround:** No

#### `strptime()`
- **YARA-L:** `timestamp.as_unix_seconds`
- **Support:** Not specified
- **Workaround:** No

#### `now()`
- **YARA-L:** `timestamp.now()`
- **Support:** Dashboard: Yes | Search: Yes | Rule: Yes
- **Workaround:** No

---

### Mathematical Functions

#### `exp()`
- **YARA-L:** Not available
- **Support:** Dashboard: No | Search: No | Rule: No
- **Workaround:** No

#### `exact()`
- **YARA-L:** Not available
- **Support:** Dashboard: No | Search: No | Rule: No
- **Workaround:** No

#### `log()`
- **YARA-L:** Not available
- **Support:** Dashboard: No | Search: No | Rule: No
- **Workaround:** No

#### `sigfig()`
- **YARA-L:** Not available
- **Support:** Dashboard: No | Search: No | Rule: No
- **Workaround:** No

#### `round()`
- **YARA-L:** `math.round()`
- **Support:** Dashboard: Yes | Search: Yes | Rule: Yes
- **Workaround:** No

#### `abs()`
- **YARA-L:** `math.abs()`
- **Support:** Dashboard: Yes | Search: Yes | Rule: Yes
- **Workaround:** No

#### `ceil()`
- **YARA-L:** `math.ceil()`
- **Support:** Dashboard: Yes | Search: Yes | Rule: Yes
- **Workaround:** No

#### `floor()`
- **YARA-L:** `math.floor()`
- **Support:** Dashboard: Yes | Search: Yes | Rule: Yes
- **Workaround:** No

#### `ln()`
- **YARA-L:** `math.log()`
- **Support:** Dashboard: Yes | Search: Yes | Rule: Yes
- **Workaround:** No

#### `pow()`
- **YARA-L:** `math.pow()`
- **Support:** Dashboard: Yes | Search: Yes | Rule: Yes
- **Workaround:** No

#### `sqrt()`
- **YARA-L:** `math.sqrt()`
- **Support:** Dashboard: Yes | Search: Yes | Rule: Yes
- **Workaround:** No

#### `sum()`
- **YARA-L:** `sum()`
- **Support:** Dashboard: Yes | Search: Yes | Rule: Yes
- **Workaround:** No

#### `pi()`
- **YARA-L:** Not available
- **Support:** Dashboard: Yes | Search: Yes | Rule: Yes
- **Workaround:** Yes
- **Details:** Use static value `3.14159265359`
- **Example:**
  ```
  SPL: pi()*num
  YARA-L2: 3.14159265359*num
  ```

---

### Multivalue Eval Functions

#### `commands()`
- **YARA-L:** Not available
- **Support:** Dashboard: No | Search: No | Rule: No
- **Workaround:** No

#### `mvappend()`
- **YARA-L:** Not available
- **Support:** Dashboard: No | Search: No | Rule: No
- **Workaround:** No

#### `mvcount()`
- **YARA-L:** Not available
- **Support:** Dashboard: No | Search: No | Rule: No
- **Workaround:** No

#### `mvfilter()`
- **YARA-L:** Not available
- **Support:** Dashboard: No | Search: No | Rule: No
- **Workaround:** No

#### `mvfind()`
- **YARA-L:** Not available
- **Support:** Dashboard: No | Search: No | Rule: No
- **Workaround:** No
- **Note:** Try `re.regex`

#### `mvindex()`
- **YARA-L:** Not available
- **Support:** Dashboard: No | Search: No | Rule: No
- **Workaround:** No

#### `mvmap()`
- **YARA-L:** Not available
- **Support:** Dashboard: No | Search: No | Rule: No
- **Workaround:** No

#### `mvrange()`
- **YARA-L:** Not available
- **Support:** Dashboard: No | Search: No | Rule: No
- **Workaround:** No

#### `mvreverse()`
- **YARA-L:** Not available
- **Support:** Dashboard: No | Search: No | Rule: No
- **Workaround:** No

#### `mvsort()`
- **YARA-L:** Not available
- **Support:** Dashboard: No | Search: No | Rule: No
- **Workaround:** No

#### `mvzip()`
- **YARA-L:** Not available
- **Support:** Dashboard: No | Search: No | Rule: No
- **Workaround:** No

#### `mv_to_json_array()`
- **YARA-L:** Not available
- **Support:** Dashboard: No | Search: No | Rule: No
- **Workaround:** No

#### `mvjoin()`
- **YARA-L:** `arrays.join_string()`
- **Support:** Dashboard: Yes | Search: Yes | Rule: Yes
- **Workaround:** No

#### `split()`
- **YARA-L:** `strings.split()`
- **Support:** Dashboard: Yes | Search: Yes | Rule: Yes
- **Workaround:** No

#### `mvdedup()`
- **YARA-L:** `array_distinct()`
- **Support:** Dashboard: Yes | Search: Yes | Rule: Yes
- **Workaround:** No

---

### Statistical Eval Functions

#### `avg()`
- **YARA-L:** `avg()`
- **Support:** Dashboard: Yes | Search: Yes | Rule: Yes
- **Workaround:** No

#### `min()`
- **YARA-L:** `min()`
- **Support:** Dashboard: Yes | Search: Yes | Rule: Yes
- **Workaround:** No

#### `max()`
- **YARA-L:** `max()`
- **Support:** Dashboard: Yes | Search: Yes | Rule: Yes
- **Workaround:** No

#### `random()`
- **YARA-L:** `math.random()`
- **Support:** Dashboard: Yes | Search: Yes | Rule: Yes
- **Workaround:** No

---

### Text Functions

#### `len()`
- **YARA-L:** `array.length()`
- **Support:** Dashboard: Yes | Search: Yes | Rule: Yes
- **Workaround:** No

#### `spath()`
- **YARA-L:** Not available
- **Support:** Dashboard: No | Search: No | Rule: No
- **Workaround:** No

#### `substr()`
- **YARA-L:** Not available
- **Support:** Dashboard: No | Search: No | Rule: No
- **Workaround:** No

#### `lower()`
- **YARA-L:** `strings.to_lower()`
- **Support:** Dashboard: Yes | Search: Yes | Rule: Yes
- **Workaround:** No

#### `ltrim()`
- **YARA-L:** `strings.ltrim()`
- **Support:** Dashboard: Yes | Search: Yes | Rule: Yes
- **Workaround:** No

#### `replace()`
- **YARA-L:** `re.replace()`
- **Support:** Dashboard: Yes | Search: Yes | Rule: Yes
- **Workaround:** No

#### `rtrim()`
- **YARA-L:** `strings.rtrim()`
- **Support:** Dashboard: Yes | Search: Yes | Rule: Yes
- **Workaround:** No

#### `trim()`
- **YARA-L:** `strings.trim()`
- **Support:** Dashboard: Yes | Search: Yes | Rule: Yes
- **Workaround:** No

#### `upper()`
- **YARA-L:** `strings.to_upper()`
- **Support:** Dashboard: Yes | Search: Yes | Rule: Yes
- **Workaround:** No

#### `urldecode()`
- **YARA-L:** `strings.url_decode()`
- **Support:** Dashboard: Yes | Search: Yes | Rule: Yes
- **Workaround:** No

---

### Stats Aggregate Functions

#### `estdc`
- **YARA-L:** Not available
- **Support:** Dashboard: No | Search: No | Rule: No
- **Workaround:** No

#### `estdc_error`
- **YARA-L:** Not available
- **Support:** Dashboard: No | Search: No | Rule: No
- **Workaround:** No

#### `exactperc()`
- **YARA-L:** Not available
- **Support:** Dashboard: No | Search: No | Rule: No
- **Workaround:** No

#### `perc`
- **YARA-L:** Not available
- **Support:** Dashboard: No | Search: No | Rule: No
- **Workaround:** No

#### `stdevp`
- **YARA-L:** Not available
- **Support:** Dashboard: No | Search: No | Rule: No
- **Workaround:** No

#### `sumsq`
- **YARA-L:** Not available
- **Support:** Dashboard: No | Search: No | Rule: No
- **Workaround:** No

#### `upperperc()`
- **YARA-L:** Not available
- **Support:** Dashboard: No | Search: No | Rule: No
- **Workaround:** No

#### `varp()`
- **YARA-L:** Not available
- **Support:** Dashboard: No | Search: No | Rule: No
- **Workaround:** No

#### `count()`
- **YARA-L:** `count()`
- **Support:** Dashboard: Yes | Search: Yes | Rule: Yes
- **Workaround:** No

#### `distinct_count()`
- **YARA-L:** `distinct_count()`
- **Support:** Dashboard: Yes | Search: Yes | Rule: Yes
- **Workaround:** No

#### `max()`
- **YARA-L:** `max()`
- **Support:** Dashboard: Yes | Search: Yes | Rule: Yes
- **Workaround:** No

#### `min()`
- **YARA-L:** `min()`
- **Support:** Dashboard: Yes | Search: Yes | Rule: Yes
- **Workaround:** No

#### `sum()`
- **YARA-L:** `sum()`
- **Support:** Dashboard: Yes | Search: Yes | Rule: Yes
- **Workaround:** No

#### `median()`
- **YARA-L:** `window.median()`
- **Support:** Dashboard: No | Search: No | Rule: Yes
- **Workaround:** Yes

#### `mode()`
- **YARA-L:** `window.mode()`
- **Support:** Dashboard: No | Search: No | Rule: Yes
- **Workaround:** Yes

#### `range()`
- **YARA-L:** `window.range()`
- **Support:** Dashboard: No | Search: Yes | Rule: Yes
- **Workaround:** Yes

#### `var()`
- **YARA-L:** `window.variance`
- **Support:** Dashboard: No | Search: No | Rule: Yes
- **Workaround:** Yes

#### `mean()`
- **YARA-L:** `avg()`
- **Support:** Dashboard: Yes | Search: Yes | Rule: Yes
- **Workaround:** No

#### `stdev()`
- **YARA-L:** `window.stdev()`
- **Support:** Dashboard: No | Search: No | Rule: Yes
- **Workaround:** Yes

---

### Event Order Functions

#### `first()`
- **YARA-L:** `window.first()`
- **Support:** Dashboard: Yes | Search: Yes | Rule: Yes
- **Workaround:** Yes

#### `last()`
- **YARA-L:** `window.last()`
- **Support:** Dashboard: Yes | Search: Yes | Rule: Yes
- **Workaround:** Yes

---

### Time Functions

#### `earliest()`
- **YARA-L:** `window.first()`
- **Support:** Dashboard: Yes | Search: Yes | Rule: Yes
- **Workaround:** No

#### `earliest_time()`
- **YARA-L:** Not available
- **Support:** Not specified
- **Workaround:** No

#### `latest()`
- **YARA-L:** `latest()`
- **Support:** Dashboard: Yes | Search: Yes | Rule: Yes
- **Workaround:** No

#### `latest_time()`
- **YARA-L:** Not available
- **Support:** Not specified
- **Workaround:** No

#### `per_day()`
- **YARA-L:** Not available
- **Support:** Not specified
- **Workaround:** No

#### `per_hour()`
- **YARA-L:** Not available
- **Support:** Not specified
- **Workaround:** No

#### `per_minute()`
- **YARA-L:** Not available
- **Support:** Not specified
- **Workaround:** No

#### `per_second()`
- **YARA-L:** Not available
- **Support:** Not specified
- **Workaround:** No

#### `rate()`
- **YARA-L:** Not available
- **Support:** Not specified
- **Workaround:** No

#### `rate_avg()`
- **YARA-L:** Not available
- **Support:** Not specified
- **Workaround:** No

#### `rate_sum()`
- **YARA-L:** Not available
- **Support:** Not specified
- **Workaround:** No

---

## Migration Insights

### Unsupported Commands (No YARA-L Equivalent)

**Comparison & Boolean:**
- `in()`
- `searchmatch()`

**Conversion:**
- `ipmask()`
- `printf()`
- `toarray()`
- `todouble()`
- `toint()`
- `tonumber()`
- `tomv()`
- `toobject()`

**Cryptographic:**
- `md5()`
- `sha1()`
- `sha512()`

**Date & Time:**
- `relative_time()`
- `earliest_time()`
- `latest_time()`
- `per_day()`
- `per_hour()`
- `per_minute()`
- `per_second()`
- `rate()`
- `rate_avg()`
- `rate_sum()`

**Mathematical:**
- `exp()`
- `exact()`
- `log()`
- `sigfig()`

**Multivalue:**
- `commands()`
- `mvappend()`
- `mvcount()`
- `mvfilter()`
- `mvfind()`
- `mvindex()`
- `mvmap()`
- `mvrange()`
- `mvreverse()`
- `mvsort()`
- `mvzip()`
- `mv_to_json_array()`

**Stats Aggregate:**
- `estdc`
- `estdc_error`
- `exactperc()`
- `perc`
- `stdevp`
- `sumsq`
- `upperperc()`
- `varp()`

**Text:**
- `spath()`
- `substr()`

---

### Commands Requiring Workarounds

- **`false()`** → Use `0`
- **`null()`** → Use empty string `""`
- **`nullif()`** → Use conditional: `if(field1=field2, "", field1)`
- **`true()`** → Use `1`
- **`validate()`** → Use nested `if()` with reversed conditions
- **`case()`** → Use nested `if()`
- **`pi()`** → Use static value `3.14159265359`
- **`median()`** → Use `window.median()` (Rule only)
- **`mode()`** → Use `window.mode()` (Rule only)
- **`range()`** → Use `window.range()` (Search/Rule only)
- **`var()`** → Use `window.variance` (Rule only)
- **`stdev()`** → Use `window.stdev()` (Rule only)
- **`first()`** → Use `window.first()`
- **`last()`** → Use `window.last()`

---

### Support Limitations by Context

**Rule Only (Not Dashboard/Search):**
- `median()` → `window.median()`
- `mode()` → `window.mode()`
- `var()` → `window.variance`
- `stdev()` → `window.stdev()`

**Search/Rule Only (Not Dashboard):**
- `range()` → `window.range()`

---

### Common Mapping Patterns

1. **String Functions:** Prefix with `strings.`
   - `lower()` → `strings.to_lower()`
   - `upper()` → `strings.to_upper()`
   - `trim()` → `strings.trim()`
   - `split()` → `strings.split()`

2. **Math Functions:** Prefix with `math.`
   - `round()` → `math.round()`
   - `abs()` → `math.abs()`
   - `ceil()` → `math.ceil()`
   - `floor()` → `math.floor()`
   - `pow()` → `math.pow()`
   - `sqrt()` → `math.sqrt()`

3. **Array Functions:** Use `arrays.` or `array.` prefix
   - `mvjoin()` → `arrays.join_string()`
   - `mvdedup()` → `array_distinct()`
   - `len()` → `array.length()`

4. **Regex Operations:** Use `re.` prefix
   - `match()` → `re.regex()`
   - `like()` → `re.regex()`
   - `replace()` → `re.replace()`

5. **Type Casting:** Use `cast.as_*()` functions
   - `tobool()` → `cast.as_bool()`
   - `tostring()` → `cast.as_string()`

6. **Hash Functions:** Only SHA256 supported
   - `sha256()` → `hash.sha256()`

7. **Timestamp Functions:** Use `timestamp.` prefix
   - `now()` → `timestamp.now()`
   - `strftime()` → `timestamp.get_timestamp`
   - `strptime()` → `timestamp.as_unix_seconds`

8. **Window Functions:** Use `window.` prefix (mostly Rule context)
   - Statistical aggregations requiring window context
   - Event ordering functions

9. **Network Functions:** Use `net.` prefix
   - `cidrmatch()` → `net.ip_in_range_cidr()`

---

### Key Behavioral Differences

1. **`tobool()` vs `cast.as_bool()`**
   - Splunk: Returns true for any non-zero integer
   - SecOps: Returns true only for integer 1

2. **`tostring()` vs `cast.as_string()`**
   - Splunk: Supports format parameters (binary, hex, commas, duration)
   - SecOps: No format support, but has optional default value parameter

3. **`validate()` Logic Reversal**
   - Splunk: Returns value when condition is FALSE
   - SecOps: Must reverse all conditions when using nested `if()`

4. **Boolean Literals**
   - Use numeric equivalents: `0` for false, `1` for true

5. **Null Handling**
   - Use empty string `""` instead of null functions
