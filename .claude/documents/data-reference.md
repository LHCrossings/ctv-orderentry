# Etere Order Entry Data Reference

Implicit domain knowledge, business rules, and data relationships for Etere
order entry automation. Read this before writing or modifying any agency script
that calls `EtereClient`.

---

## Core Domain Model

Etere is the traffic/automation system used to book, schedule, and manage
television advertising spots. The fundamental workflow is:

1. **Contract header** — top-level agreement for a client/campaign
2. **Contract lines** — individual scheduling instructions within a contract
   (one line per market, daypart, rate, or spot type)
3. **Programming blocks** — specific time slots within a line where spots are
   eligible to air

An **order** in agency-speak maps to a **contract** in Etere. A **line item**
maps to a **contract line**. One contract may have many lines across different
markets, dayparts, or date ranges.

---

## The Master Market / Line Market Distinction

**This is the most common source of mis-entered contracts.**

Every Etere session has a **master market** — the station context the user is
logged into. Set once via `set_master_market(market)` before contract creation.

Every **contract line** also has its own market, set via the
`selectedschedStation` dropdown inside the line form.

Rules:
- Master market is **always `"NYC"`** except for Dallas (WorldLink) where it
  is **`"DAL"`**.
- Must be set *before* navigating to `/sales/new` — has no retroactive effect
  on already-created lines.
- The line-level `market` argument to `add_contract_line()` controls which
  station the spot airs on. The two values are independent and both must be
  correct.
- For MMT (multi-market) campaigns, master market is still `"NYC"`. Each line
  carries its own market code.

---

## Market Codes

Used in agency data files and `EtereClient.MARKET_CODES`.

| Code | Market                        | Integer ID |
|------|-------------------------------|------------|
| NYC  | New York City / New Jersey    | 1          |
| CMP  | Chicago / Minneapolis         | 2          |
| HOU  | Houston                       | 3          |
| SFO  | San Francisco                 | 4          |
| SEA  | Seattle                       | 5          |
| LAX  | Los Angeles                   | 6          |
| CVC  | Central Valley / Sacramento   | 7          |
| WDC  | Washington DC                 | 8          |
| MMT  | Multimarket National          | 9          |
| DAL  | Dallas (Asian Channel only)   | 10         |

**Crossings TV** uses: CVC, SFO, LAX, SEA, HOU, CMP, WDC, MMT, NYC
**The Asian Channel** uses: DAL (plus any shared markets)

---

## Language Codes

Block-tab filter prefixes. Must match exactly the string beginning a block name
in Etere (format: `"<PREFIX> - <Block Name>"`).

| Code | Language    |
|------|-------------|
| M    | Mandarin    |
| C    | Cantonese   |
| P    | Punjabi     |
| SA   | South Asian |
| T    | Filipino    |
| V    | Vietnamese  |
| Hm   | Hmong       |
| J    | Japanese    |
| K    | Korean      |

Pass `block_prefixes=["M", "C"]` to `add_contract_line()` to select only
Mandarin and Cantonese blocks. `None` or empty list skips block filtering.

---

## Spot Codes

| Label           | Integer | Notes                         |
|-----------------|---------|-------------------------------|
| Paid Commercial | 2       | Standard bought spot          |
| BNS / Bonus Spot| 10      | Uncompensated added spot      |

Use `SPOT_CODES` from `EtereClient` or pass the integer to
`add_contract_line(spot_code=...)`. Never hard-code the integer in agency files.

---

## Customer Database

Customer records live in `data/customers.db` (SQLite). Key fields that drive
contract entry defaults:

| Column                  | Type    | Purpose                                          |
|-------------------------|---------|--------------------------------------------------|
| `customer_id`           | TEXT    | Etere's internal customer ID                    |
| `customer_name`         | TEXT    | Display / lookup name                            |
| `order_type`            | TEXT    | Primary key alongside `customer_name`            |
| `code_name`             | TEXT    | Prefix for contract code field                   |
| `description_name`      | TEXT    | Prefix for contract description field            |
| `include_market_in_code`| INTEGER | 1 = append market short code to contract code    |
| `billing_type`          | TEXT    | `'agency'` or `'direct'`                         |
| `separation_customer`   | INTEGER | Customer separation in minutes (default 15)      |
| `separation_event`      | INTEGER | Event separation (default 0)                     |
| `separation_order`      | INTEGER | Order separation (default 0)                     |
| `default_market`        | TEXT    | Optional default market code                     |

Market short codes used in contract codes: `CVC→CV`, `SFO→SF`, `SEA→SEA`.

Schema migrations run automatically via `_migrate_schema()` in
`customer_repository.py` — safe to re-run; backward-compatible.

Manage records: `python scripts/manage_customers.py`

---

## Contract Code Conventions

- `code_name` + optional market short code → contract code prefix
- `description_name` → contract description prefix
- Codes must be **unique** within Etere; reusing a prior-flight code will conflict
- `create_contract_header()` accepts a free-text `code` string — Etere validates
  uniqueness only, not format

---

## Customer ID vs. Customer Search

Two ways to attach a customer to a contract:

1. **Direct ID** (`customer_id=<int>`): Fastest. Use when the Etere customer
   ID is known and stored in the customer DB.
2. **Manual search** (`customer_id=None`): Pauses execution and prompts the
   operator to use the browser modal. Use when the ID is unknown or a new
   customer may need to be created.

Never pass `customer_id=0` or an empty string — this silently creates a
contract with no customer attached.

---

## Charge To / Invoice Header

Select2 dropdowns populated from Etere master data. Values must match the
option text **exactly** (case and spacing).

Defaults:
- `charge_to = "Customer share indicating agency %"`
- `invoice_header = "Agency"`

If an option is not found, `EtereClient` logs a warning and continues — the
field will be blank, which can cause downstream invoicing issues.

When adding a new agency, verify the exact option strings by inspecting the
dropdown in a live Etere session before hardcoding them.

---

## Contract Line Field Calculations

### Spots Per Week vs. Total Spots

`spots_per_week` is the weekly cadence. `total_spots` is the count across the
entire flight. If `total_spots` is not supplied, `EtereClient` uses
`spots_per_week` as a fallback — **this is wrong for multi-week flights**.
Always calculate and pass `total_spots` explicitly:

```python
total_spots = weeks_in_flight * spots_per_week
```

### Max Daily Run (Auto-Calculation)

If `max_daily_run` is `None`, `EtereClient` calculates:

```
max_daily_run = ceil(spots_per_week / active_days_in_pattern)
```

Example: 14 spots/week over M–Sa (6 days) → `ceil(14/6)` = 3/day.

Override when the source PDF specifies an explicit daily cap.

### Separation Intervals

Tuple format: `(customer_minutes, event_minutes, order_minutes)`

Map to Etere UI fields (Selenium):
- `contractLineGeneralIcomm` — customer separation
- `contractLineGeneralIevent` — event separation
- `contractLineGeneralIsster` — order separation

Map to CONTRATTIRIGHE columns (direct DB):
- `Interv_Committente` — customer separation
- `INTERVALLO`         — **order** separation ⚠ old Etere web labeled this "Event" (bug — now fixed)
- `INTERV_CONTRATTO`  — **event** separation ⚠ old Etere web labeled this "Order" (bug — now fixed)

| Scenario                     | Value       | Notes                              |
|------------------------------|-------------|------------------------------------|
| Default (most campaigns)     | `(15, 0, 0)`| Industry standard                  |
| TCAA contracts               | `(10, 0, 0)`| Contract specifies 10 min          |
| Billboard spots              | `(0, 0, 0)` | Airs first in break — no separation|

Override from customer DB fields (`separation_customer`, etc.) when present.

---

## Billboards vs. Bookends

Both are line-level properties requiring separate contract lines even if daypart
and market are identical.

**Bookend** (`is_bookend=True`): Places spot at both top and bottom of break
(scheduling type 6 in Options tab).

**Billboard** (`is_billboard=True`):
- `:05`/`:10` spots that air immediately before a `:30` in the same break
- Auto-detected: if a `:30` line exists in the same time window, shorter
  durations in that window are flagged as billboards
- Separation forced to `(0, 0, 0)`
- Description reformatted: `"{days} BILLBOARD {program}"`
- Scheduling type 4 (Top of break)

---

## Time Parsing Rules

`EtereClient.parse_time_range(time_str)` normalizes any common format to
24-hour `HH:MM`. Rules agency scripts must respect:

| Rule               | Detail                                                               |
|--------------------|----------------------------------------------------------------------|
| **Floor**          | Nothing earlier than `06:00`. `5:30a` → `06:00`                     |
| **Ceiling**        | Nothing later than `23:59`. Midnight (`12a`, `1a`–`5a`) → `23:59`   |
| **12:00a / 12a**   | Always `23:59` (end of broadcast day)                                |
| **12:00p / 12n**   | Always `12:00` (noon)                                                |
| **PM inference**   | If only end time has `p/a`, start inherits same period unless start hour > end hour (e.g., `11-130p` → 11am–1:30pm) |
| **Semicolons**     | `"4p-5p; 6p-7p"` → earliest start + latest end (`16:00`–`19:00`)    |

---

## The Sunday 6–7am Rule

**Universal across all agencies and markets.**

Sunday 6:00am–7:00am is reserved for paid programming. If a line's day
pattern includes Sunday and the time window is exactly `6:00a–7:00a`, Sunday
must be removed from the pattern.

```python
days, active_day_count = etere.check_sunday_6_7a_rule(days, time_str)
# Returns days with Sunday stripped and updated active day count
```

Call this before passing `days` to `add_contract_line()`.

---

## Day Pattern Conventions

| Pattern  | Days Included           | Active Day Count |
|----------|-------------------------|-----------------|
| M-Su     | Monday–Sunday (all 7)   | 7               |
| M-F      | Monday–Friday           | 5               |
| M-Sa     | Monday–Saturday         | 6               |
| Sa-Su    | Saturday–Sunday         | 2               |
| Sa / SAT | Saturday only           | 1               |
| Su / SUN | Sunday only             | 1               |

`_count_active_days()` recognizes these patterns. Unrecognized patterns default
to 7 — verify the pattern string matches exactly before assuming the count.

---

## Week Consolidation

Two consolidation helpers convert per-week spot counts into the contiguous date
ranges that Etere contract lines require.

### `consolidate_weeks(weekly_spots, week_start_dates, flight_end)`

Used when week dates come directly from the parsed document (SAGENT, Charmaine,
GaleForce, RPM).

- `weekly_spots`: list of per-week spot counts (int)
- `week_start_dates`: `List[str]` (e.g., `"Apr 27"`) **or** objects with a
  `.start_date` attribute (e.g., `CharmaineWeekColumn`)
- Splits into separate Etere lines on: different spot count **or**
  non-consecutive weeks (gap > 7 days)
- Returns: `List[dict]` with keys `start_date`, `end_date`, `spots_per_week`,
  `weeks`

### `consolidate_weeks_from_flight(weekly_spots, flight_start, flight_end)`

Used when weeks must be derived from a continuous flight range (TCAA). Generates
week boundaries at 7-day increments from `flight_start`.

---

## Broadcast Calendar Rules

Broadcast weeks run **Monday–Sunday** (not Sunday–Saturday).

A new broadcast month begins on the **Monday of the week containing the 1st**
of that calendar month.

Examples:
- Aug 1, 2025 = Friday → broadcast August starts **Monday July 28**
- Date range header `"7/28–8/31"` = entire broadcast month of August

**All weeks under a cross-month range header belong to the end month** for
billing purposes. The Etere flight start date is still the actual calendar date.

**Resolving day numbers in a cross-month header:**
- day ≥ range start_day → use start month
- day < range start_day → use end month

Example with `"7/28–8/31"`:
- day=28 → 28 ≥ 28 → July 28 (broadcast August)
- day=4 → 4 < 28 → August 4 (broadcast August)

---

## EtereClient Architecture Rules

- **Agency files pass DATA** — they never import Selenium, call
  `driver.find_element`, or navigate URLs directly.
- **All field IDs live in `etere_client.py`** — UI changes are fixed once here;
  all agencies benefit automatically.
- **`EtereClient` receives a live `webdriver.Chrome` instance** — login, market
  selection, and logout are the calling script's responsibility.
- **Always call `logout()`** before closing the browser. Etere locks accounts
  on concurrent sessions; skipping logout causes the next run to fail.
- **Separation defaults come from the customer DB** — always check
  `separation_customer/event/order` fields before falling back to `(15, 0, 0)`.

---

## What EtereClient Does NOT Handle

- PDF parsing — agency script's responsibility
- Spot-level break placement — Etere's internal scheduler handles this after
  lines are created
- Affidavit confirmation or post-broadcast reconciliation
- Invoice generation — contracts are entered; billing is a separate Etere workflow
- Nielsen ratings or CPM calculations — negotiated offline, rates entered manually
