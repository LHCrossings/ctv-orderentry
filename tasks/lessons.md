# Lessons Learned — Active Rulebook

Core lessons that apply to all new parsers and ongoing work. Parser-specific quirks and historical bugs are in `lessons-archive.md`.

---

## Multi-Flight Traffic PDFs: Track Dates Per-Spot, Never at the Instruction Level

**Session:** HL traffic parser — Toyota June 2026 ACM #13933 R1 (2026-06-26)

**Rule:** A single traffic-instruction PDF often carries **several flights** (e.g. 6/2–6/8, 6/9–6/30, 6/30–7/6), each with its **own ISCI per dialect** (the same dialect gets a different creative each flight). The flight dates therefore belong on the **spot/ISCI**, not on the instruction. Two failure modes if you store one date range for the whole PDF:
1. Every spot inherits the header's full-flight range, so each creative is matched against the entire flight instead of its own window.
2. Downstream code that keys a `dialect → filmati` map collapses the flights — the last creative for a dialect overwrites the earlier two, and the right spots get the wrong creative.

**How to apply (traffic parsers + the `/traffic/assign-assets` route):**
1. Put `date_from_sql/date_to_sql/start_date/end_date` on the **spot** dataclass (`HLTrafficSpot`), parsed from that spot's own row. Keep instruction-level dates only for display (use the header EXACT FLIGHT DATES = full flight).
2. In the route, group found spots by `(system_dialect, date_from, date_to)` → one `dialect_assignment` per group, and put that group's **own** date range into `filters` (`date_from`/`date_to`). `_build_spot_filter` then counts/assigns only spots inside that window. Never reduce to `{dialect: filmati}`.
3. Many HL rows are **single-line** (ISCI, title, `(Dialect)`, dur, rotation, dates all on one line) — scan the **whole block** (line 1 + following) for the date pair, and take the **first** pair (a trailing `@ 12 NOON`/`@ 1201p` annotation must not shift the window).
4. **Multi-page bleed:** block-grouping that appends every non-ISCI line to the current block lets the last ISCI on a page absorb the *next* page's header (incl. its `EXACT FLIGHT DATES`). Close the open block on end-of-table markers (`Link to new spots`, `Page N of`).
5. The same collapse pattern exists in the **RPM** branch (`format == 'rpm'`) of the route — fix it the same way if a multi-flight RPM PDF appears.

**Verify:** parse → assert N distinct date windows; then run the real per-group COUNT query against the matched contracts and confirm the same dialect routes *different* spot counts to *different* windows.

---

## Format Detectors Must Not Hinge on a Single Encoding Trait — Detect by Content, Not Font

**Session:** Toyota CRSF-TV Q3 BDR parse failure (2026-06-24)

**Rule:** When a detector keys on an *encoding* artifact (custom font, `(cid:)` garble, rotation, image-only page) rather than the *content* of the document, it silently misroutes the day the source system changes its export. A new, valid file fails with **zero estimates and no error** — the worst kind of failure.

**What happened:** `is_bdr_pdf()` detected H/L Buy Detail Reports *only* by a Type3 custom-font fingerprint. H/L started exporting clean-text BDRs (normal embedded font, extractable text). Those fell through to the generic `hl_parser`, which can't read the BDR layout → returned `[]` silently. Compounding it, `parse_bdr_pdf` was OCR-only (always rasterize + rotate), so even called directly it produced garbage on the un-rotated clean PDF.

**How to apply:**
1. **Detect by content with a self-validating signature.** Add a text-based check (`is_bdr_text`) that matches the actual *row layout* (BDR rows are day-pattern-first, no line number, no daypart code). A layout guard means it won't steal sibling formats (`hl_parser` rows are line-numbered) even when header markers ("Buy Detail Report", "H/L Agency") overlap. Keep the font-fingerprint check too — it still catches the old Type3 variant cheaply.
2. **Text-source must degrade gracefully.** Parsers that OCR should try `pdfplumber.extract_text()` first and fall back to OCR only when the text is `(cid:`-garbled or < ~50 chars. Never assume a format always needs OCR.
3. **Order matters:** check the more-specific format before the format it shares markers with (`_is_bdr` before `_is_hl_partners` in `detect_from_text`).

**Why:** Two parsers (`hl_parser`, `hl_bdr_parser`) share the same agency markers and differ only in table layout. The discriminator must be the layout, available in the extractable text — never a transient encoding trait.

---

## New Parser Checklist for Direct DB (All Future Parsers Are Direct DB)

**Session:** Pink-pill testing sweep (2026-06-09)

**Rule:** We no longer write Selenium order-entry parsers. Every new parser is direct DB. When building one, apply ALL of the following from the start — these were all discovered as bugs during the 2026-06 testing sweep:

### 1. Duration: always pass `str(seconds)`, never `f":{sec:02d}"`
`_duration_str_to_seconds()` in `etere_direct_client.py` splits on `:` — a leading colon (e.g. `":30"`) produces `['', '30']` and `int('')` crashes. Pass bare integer strings: `str(spot_duration)` (e.g. `"30"`, `"45"`).

### 2. `contracts` list must be populated on success; use gathered code, not DB ID
`ProcessingResult.contracts` must contain at least one `Contract(contract_number=order_code, order_type=OrderType.X)` when `success=True`. Never return `contracts=[]` on success — the final summary will show "0 contracts created" even if Etere has the data.

Use the **gathered contract code** (from `user_input.get('contract_code')`), not the Etere DB integer ID. Pattern:
```python
inp = order.order_input
label = (inp.get('contract_code') if isinstance(inp, dict) else None) or str(contract_id)
contracts = [Contract(contract_number=label, order_type=OrderType.X)] if success else []
```

**Do NOT set `etere_id` yourself** (2026-06-25). `OrderProcessingService._enrich_results()` runs once per batch and auto-resolves the Etere DB contract ID from each contract's code (`CONTRATTITESTATA.COD_CONTRATTO`), so every parser's pre-close and final summaries print `Contract <code> (ID: NNNN)` like WorldLink — for free. Just keep returning the gathered code as `contract_number`; the ID appears automatically. (WorldLink still sets `etere_id` itself; it's skipped by the enricher, which only fills `etere_id is None`.)

**Multi-contract parsers (one PDF → many contracts): the automation must RETURN the codes** (2026-06-26). A `bool` return throws away which contracts were created, so the handler can only report `contracts=[]` → "0 contract(s)" even on success. For any parser that loops creating >1 contract (Impact = per-quarter, H&L = per-estimate, Charmaine = per-order), change `process_X_order()` to return `list[str]` of created codes (append the code right after each header is created). Empty list = failure — **truthiness is preserved**, so existing `success = process_X_order(...)` callers keep working. Handler then does `contracts = [Contract(contract_number=c, order_type=OrderType.X) for c in codes]`. For autocommit parsers (Charmaine, H&L) return the codes actually created (reflects DB reality even on partial failure); for single-transaction parsers (Impact) the list is all-or-nothing. If the automation already returns the code (e.g. DART returns the contract number), just **use it** instead of discarding it.

**Audit technique** (2026-06-26): to find this bug across all handlers, AST-walk `_process_*_order` methods and flag any `ProcessingResult(...)` return where `success` is not literal `False` but `contracts` is `[]` / never appended-to. This sweep found 5 affected parsers (HL, Impact, RPM, DART, Charmaine) after the iGraphix report. (It also surfaced that the Impact handler was passing a non-existent `user_input=` kwarg to `process_impact_order` — a latent `TypeError` — now `pre_gathered_inputs=`.)

### 3. `booking_code` must always be explicit — never rely on `is_bonus`
Pass `booking_code=10 if is_bonus else 2` to every `add_contract_line()` call. `is_bonus=True` only sets the scheduling type; it does NOT set the booking code.

### 4. Customer ID must be resolved in `gather_*_inputs()`, not during processing
All user-interactive prompts (customer ID, order code, description) belong in the upfront gather function registered in `_INPUT_GATHERERS`. If `_resolve_customer_id()` or any `input()` call fires during processing, move it to gather.

### 5. `gather_*_inputs` must return a dict; service uses `user_input.get('key')`
Service methods check `isinstance(inp, dict)` and use `.get('order_code')` / `.get('contract_code')`. The gathered dict must use the correct key so the contracts-list builder can find it.

### 6. Yes/Enter at a date-override prompt must keep the original date
Pattern: `actual = raw if raw and raw.lower() not in ('y', 'yes') else original`. Never do `actual = raw if raw else original` — typing "yes" stores the string "yes" as the date.

### 7. Service/bridge registration — 3 files, all at once
**Updated 2026-06-10:** All new parsers are direct DB. Add to ALL THREE simultaneously:
1. `_DIRECT_DB_ORDER_TYPES` in `order_processing_service.py`
2. `_DIRECT_DB_KEYS` in `parser_bridge.py`
3. `_DIRECT_DB_TESTED_KEYS` in `parser_bridge.py`
Missing step 1 causes a browser session to be opened. Missing steps 2–3 hides the parser from the web UI entirely.

### 8. `gather_*_inputs` must prompt for contract code and description
Every gather function must ask the user for the contract code and description before processing starts. Never let the processing function prompt for these or auto-generate them silently.

### 9. All gather prompts must use the bracket-default pattern
Every user-facing prompt in a `gather_*_inputs` function must use this pattern:
```python
raw = input(f"  Contract code [{default_code}]: ").strip()
contract_code = raw or default_code
```

**Never** use the two-step "Use default? (y/n)" / "Enter X:" pattern — it doubles the keystrokes and is inconsistent across parsers.

### 10. Do not inline-prompt for separation in `gather_*_inputs`
The orchestrator calls `_confirm_separation(inputs)` after every `gather_*_inputs` call. Any parser that also prompts for separation inside `gather_*_inputs` causes a **double prompt**.

Just set `inputs['separation'] = separation` from the customer DB defaults and return it — the orchestrator handles the user-facing confirmation.

### 11. CustomerRepository API — always use the entity pattern, never dict-style upsert

**Session:** ACM parser (2026-06-11)

The `CustomerRepository` class requires these exact call patterns:

**Lookup:**
```python
import os
from src.data_access.repositories.customer_repository import CustomerRepository
from src.domain.enums import OrderType

if not os.path.exists(CUSTOMER_DB_PATH):
    return None
repo = CustomerRepository(CUSTOMER_DB_PATH)
cust = repo.find_by_name(client_name, OrderType.X) or repo.find_by_name_any_type(client_name)
```

**Reading fields (attributes, NOT dict `.get()`):**
```python
customer_id = cust.customer_id
separation  = (cust.separation_customer, cust.separation_event, cust.separation_order)
code_name   = cust.code_name or 'DEFAULT'
billing_type = cust.billing_type or 'direct'
```

**Save (requires a `Customer` entity):**
```python
from src.domain.entities import Customer
repo.save(Customer(
    customer_id=str(customer_id),
    customer_name=client_name,
    order_type=OrderType.X,
    billing_type='agency',
    separation_customer=separation[0],
    separation_event=separation[1],
    separation_order=separation[2],
))
```

### 12. Never use `%-m` / `%-d` in strftime — Linux-only, crashes on Windows

**Session:** ACM parser (2026-06-11)

The `%-m` and `%-d` strftime directives are **Linux/macOS only**. On Windows they raise `ValueError`.

**Wrong:** `f"{d.strftime('%-m/%-d/%y')}"`
**Correct:** `f"{d.month}/{d.day}/{d.strftime('%y')}"`

Use `.month`, `.day`, `.year` integer attributes directly.

### 13. `billing_type` must be read from the customer DB record, never hardcoded

**Session:** ACM parser (2026-06-11)

The customer DB record stores `cust.billing_type` (`"agency"` or `"direct"`). **Never hardcode** it in a gather or automation function — the customer record is the source of truth.

**Pattern in gather:**
```python
billing_type = cust.billing_type or 'direct'   # read from DB
```

### 14. Confirm the start date when the order starts tomorrow or earlier

**Session:** Start-date sanity check (2026-06-15)

Every new parser must check the order's flight start date during `gather_*_inputs()`. If the earliest start date is **tomorrow or earlier**, prompt the user to confirm before processing continues. An order that starts today/past usually means late IO, mis-parsed date, or backfill that needs special handling.

**How to apply:**
```python
from datetime import date, timedelta

if earliest_start <= date.today() + timedelta(days=1):
    print(f"  ⚠ This order starts {earliest_start.month}/{earliest_start.day}/{earliest_start.strftime('%y')} "
          f"(today is {date.today().month}/{date.today().day}/{date.today().strftime('%y')}).")
    raw = input(f"  Confirm start date [{earliest_start.month}/{earliest_start.day}/{earliest_start.strftime('%y')}]: ").strip()
    if raw and raw.lower() not in ('y', 'yes'):
        earliest_start = _parse_user_date(raw)
```

**Critical:** The override must reach the LINE dates, not just the contract header. Keep the original parsed start, and shift any range that begins on the original earliest start to the overridden date:
```python
date_from = _parse_date(rng['start_date'])
if original_start and override_start and override_start != original_start and date_from == original_start:
    date_from = override_start
```

### 15. Agency parsers: agency ≠ customer — hardcode the agency, look up the customer, let ANAGRAF win

**Session:** Brentan Media Services parser (2026-06-15)

For an **agency parser** (one media agency placing orders for many different advertisers), the **agency** and the **customer/advertiser** are two distinct ANAGRAF records.

- **Customer / advertiser** = who the campaign is for (ANAGRAF customer ID)
- **Agency** = the buyer placing the order (hardcoded in `AGENCY_IDS` in `etere_direct_client.py`)

**The rule:** *Always query ANAGRAF for the client, and if ANAGRAF returns an agency for that client, use it.* The hardcoded agency ID is only a fallback for rare clients with no agency linked.

**How to apply:**
```python
client.create_contract_header(
    code=..., description=..., customer_id=int(customer_id),
    agency_id=AGENCY_IDS["BRENTAN"],   # fallback only
    lookup_customer_defaults=True,     # always query ANAGRAF for the client
    contract_date=..., contract_end_date=..., billing_type=billing_type, allow_rename=True,
)
```

The gather function looks up / prompts for the **customer name only**, never the agency.

### 16. The advertiser/client name may live in the FILENAME, not the workbook

**Session:** Brentan Media Services parser (2026-06-15)

Some proposal workbooks carry only the **agency** in their cells; the **advertiser** appears only in the file name, e.g. `Crossings TV CA Conservation Corps_Brentan Media_2026.xlsx`. Extract it from the filename and let the user confirm/override it in `gather_*_inputs()`:

```python
m = re.search(r'crossings\s+tv\s+(.+?)\s*_\s*brentan', Path(path).stem, re.IGNORECASE)
client = m.group(1).strip() if m else ""
# in gather: raw = input(f"  Customer / advertiser name [{client}]: ").strip(); client = raw or client
```

### 17. Stop grid parsing at the totals/"Summary" section — don't rely on a market-name skip-set

**Session:** Brentan Media Services parser (2026-06-15)

Multi-market proposal grids end with a **"Summary of investment"** block. These rows can look like data rows but are totals/added-value notes.

**Rule:** `break` out of the row loop when you hit the summary header (`cell.lower() == 'summary of investment'`). Everything below it is never airtime lines. Always verify parsed totals reconcile against the order's own summary footer before shipping.

---

## `parse_day_bits` (DirectDB) and `_select_days` (Selenium) Must Stay in Sync

**Session:** Admerasia DirectDB conversion (2026-06-08)

**Rule:** Two day-parsing implementations must stay in sync:
- `EtereClient._select_days()` — Selenium path (etere_client.py). The **original** reference.
- `parse_day_bits()` — DirectDB path (etere_direct_client.py). Must support **all the same aliases**.

**How to apply:**
1. Any time you add a day alias to `_select_days`, also add it to `_TOKEN_MAP` and `_TOKEN_TO_INDEX` in `etere_direct_client.py`.
2. When you convert a parser to DirectDB, print the `days` string each line will pass to `add_contract_line` and confirm `parse_day_bits` produces at least one `True` flag. A line where all flags are False will silently enter but never schedule.
3. The canonical full alias set is in `_select_days` — treat it as the source of truth.

**Known full single-char set:** M=Monday, T=Tuesday, W=Wednesday, R=Thursday, F=Friday, S=Saturday, U=Sunday.

### `parse_day_bits` comma branch must expand range segments, not just single tokens

**Session:** WorldLink contract 2899 — "M-F,Su" entered Sunday only (2026-06-25)

**Bug:** A mixed pattern like `"M-F,Su"` failed the whole-string range `fullmatch` (the comma breaks it), fell into the comma-list branch, split into `["M-F", "SU"]`, and ran `_TOKEN_MAP.get("M-F")` → `None`. Only `SU` survived → Sunday-only line. Block auto-load then loaded only Sunday blocks, so the M–F airtime silently never scheduled.

**Fix:** Treat each comma segment uniformly — it may itself be a range *or* a single token. `parse_day_bits` now splits on commas and runs `_apply_day_segment()` (range-aware) on each piece. Handles `"M-F,Su"`, `"M-F,Sa-Su"`, pure ranges, and pure token lists with one code path.

**Why not delegate to `day_utils.tokenize`?** Tempting (it's the richer parser the Selenium path uses), but `day_utils` is **case-sensitive** and relies on mixed case to tokenize concatenated forms (`"MTuWThF"` → `M,Tu,W,Th,F`). `parse_day_bits` uppercases its input, and uppercase `"TU"` would greedily tokenize as `T`+`U` = Tuesday+**Sunday**. The two parsers have incompatible case contracts — keep `parse_day_bits` self-contained with its uppercase `_TOKEN_MAP`.

---

## Language-Targeted Traffic Instructions Must Use Day/Time Window Filters — Never Line Description Matching

**Session:** RPM Thunder Valley (2026-06-02)

**Rule:** Any traffic instruction format that assigns spots per language (Cantonese, Mandarin, Vietnamese, etc.) must use `_CTV_LANG_WINDOWS` (or `_DAL_LANG_WINDOWS` for The Asian Channel) time-window filters. Never attempt to detect language by matching against contract line descriptions.

**Why:** Line descriptions are free-text and change. Time windows are the ground truth: a spot that airs Monday 19:00–20:00 is Cantonese because that's what Crossings TV programs in that slot.

---

## Showing/Hiding `<tr>` Elements in JavaScript Requires `display='table-row'`

**Session:** Make Goods (2026-05-29)

**Rule:** To show a `<tr>` that has `display: none` in CSS, set `element.style.display = 'table-row'` explicitly. Setting it to `''` only works if the element has no CSS rule hiding it.

```js
// Wrong — reverts to CSS display:none
row.style.display = '';

// Correct
row.style.display = 'table-row';
```

For `<div>`, use `'block'` or `'flex'`.

---

## Etere Blacklist — Complete Reference

**Sessions:** Missing Materials blacklist button (2026-05-29) + Make Goods reconciliation (2026-05-29)

### The Accounting Formula

For any contract line, at all times:

```
N_PASSAGGI  =  trafficPalinse rows  +  TSL.PassageMiss
(ordered)       (placed/aired)          (blacklisted)
```

If `trafficPalinse + TSL.PassageMiss < N_PASSAGGI`, there are **orphaned deletions** — spots that were removed from the schedule but whose blacklist count was never written.

**Source of truth:** `trafficPalinse` (not TPALINSE, not N_PASSAGGI).

### How to Blacklist a Spot (one spot per call)

```sql
-- Step 1: delete from schedule
DELETE FROM trafficPalinse WHERE id_tpalinse = %s
DELETE FROM TPALINSE        WHERE ID_TPALINSE = %s

-- Step 2a: first blacklist on this line → INSERT
INSERT INTO Traffic_ScheduleList (
    ID_ContrattiRighe, BlackList, PassageMiss,
    ID_TRAFFICPALINSE, Date, ToDate,
    Notes, Operator,
    ID_FILMATI, ID_FILMATI_TAIL, ID_FILMATI_MIDDLE,
    ID_FATTURAEMITTENTE, Split
) VALUES (%s, 1, 1, %s, %s, %s, %s, %s, -1, -1, -1, 0, 0)

-- Step 2b: subsequent blacklist on same line → INCREMENT (never skip)
UPDATE Traffic_ScheduleList
SET PassageMiss = PassageMiss + 1
WHERE ID_ContrattiRighe = %s AND BlackList > 0
```

**Critical rules:**
- `ID_TRAFFICPALINSE` = `trafficPalinse.id_trafficPalinse` of the deleted row
- `Date` / `ToDate` = **always** `CONTRATTIRIGHE.DATA_INIZIO` / `DATA_FINE` — **never leave NULL**
- INSERT on first occurrence; `PassageMiss + 1` on every subsequent spot for that line (never skip)
- If `Date`/`ToDate` are NULL, that blacklisted spot is invisible in every date-range query

### What NOT to Do

- ❌ `LIVELLO=666` alone — Etere counts 666 rows AND TSL separately → doubles blacklist count
- ❌ TSL-only (leaving LIVELLO=0) → trafficPalinse count stays high, placed + blacklisted > N_PASSAGGI
- ❌ Skipping TSL write if entry already exists → orphaned deletions
- ❌ Leaving TSL Date/ToDate NULL → spot disappears from all date range reports

---

## Every Traffic Assignment Must Populate CONTRATTIFILMATI (the "Rotate with the following assets" pool)

**Session:** Tatari/MA Woof/Pholicious fix (2026-05-28)

**Rule:** Any code that assigns traffic (via `auto-assign`, `assign`, or `assign-spots`) **must** ensure `CONTRATTIFILMATI` is populated for every assigned line. Two requirements:

1. **`MaterialAddToAssetListC`** — the Etere HTTP call that registers filmati in the contract pool. Must be called for each filmati not yet in the pool for the specific lines being assigned. Endpoint: `POST /Sales/MaterialAddToAssetListC` with `{"idFilmatiList": [fid], "idct": contract_id}`.

2. **`CONTRATTIFILMATI` rows** — one row per `(ID_CONTRATTIRIGHE, ID_FILMATI)` with `PERCROTATION = 0`. Use DELETE+INSERT (not UPDATE+INSERT-if-rowcount) so new lines still get their rows. **Do NOT calculate or set PERCROTATION** — actual rotation percentages are set separately. The pool rows just need to exist.

3. **Cleanup DELETE must EXCLUDE assigned lines.** The cleanup that removes rows for non-assigned lines MUST include `AND ID_CONTRATTIRIGHE NOT IN ({assigned_line_ids})` — otherwise it deletes the rows just inserted.

**Mandatory checklist:** Before shipping any new traffic instruction format, verify:
1. The assignment path calls `MaterialAddToAssetListC` for each filmati being registered.
2. `CONTRATTIFILMATI` rows are written for every `(ID_CONTRATTIRIGHE, ID_FILMATI)` pair on assigned lines.

---

## Direct DB Entry Must Always Pass `booking_code` Explicitly

**Session:** iGraphix + WorldLink BNS fix (2026-05-28)

**Rule:** Any direct DB automation call to `add_contract_line()` must always pass `booking_code` explicitly:
```python
booking_code=10 if is_bonus else 2
```

Never rely on `is_bonus=True` to set the booking code automatically.

---

## Bonus / Added-Value Is ALWAYS Rotation — Even When a Caller Passes `scheduling_type`

**Session:** WorldLink AATV BNS-as-Priority fix (2026-06-25)

**Rule:** A BNS (bonus) or AV line must schedule as **Rotation (PRENOTAZIONE=1)**, never Priority — unless it's position-locked (bookend/billboard/bottom). This is enforced centrally in `add_contract_line()` and **overrides any explicit `scheduling_type` the caller passes.**

**What happened:** `worldlink_automation.py` passes `scheduling_type=0` (Priority) on *every* line. `add_contract_line` used to honor an explicit `scheduling_type` verbatim (`if scheduling_type is not None: prenotazione = scheduling_type`), which **bypassed** the bonus→Rotation rule — so all 69 bonus lines across 11 contracts entered as Priority and couldn't be scheduled as intended.

**How to apply:**
1. In `add_contract_line`, the bonus/AV rule is checked **before** the caller's `scheduling_type`:
   ```python
   _is_position_locked = is_bookend or is_billboard or is_bottom
   if (is_bonus or is_added_value) and not _is_position_locked:
       prenotazione = 1            # system rule — wins over scheduling_type
   elif scheduling_type is not None:
       prenotazione = scheduling_type
   ...
   ```
2. Paid lines still honor the caller's `scheduling_type` (WorldLink paid stays Priority).
3. To repair already-entered bonus lines: `UPDATE CONTRATTIRIGHE SET PRENOTAZIONE=1 WHERE ID_BOOKINGCODE=10 AND CONTROLLACAPOFILA=0 AND CONTROLLAFINEFILA=0 AND PRENOTAZIONE<>1` (scoped to the affected contracts). capofila/finefila already 0 and priorita 500, so flipping PRENOTAZIONE alone is the complete Priority→Rotation transition.

---

## EtereDirectClient SP Calls Must Use `self._ph`, Not Hardcoded `?`

**Session:** Trade entry direct DB write (2026-05-21)

**Rule:** Any SQL string in `etere_direct_client.py` that contains `?` placeholders must be executed as `cursor.execute(sql.replace('?', self._ph), params)`. Never hardcode `?` and call `.execute(sql, params)` directly — it will break on pymssql connections (which require `%s`).

**Applies to:** Both SP calls in `etere_direct_client.py` (header + line inserts), and any future SP calls added to the file.

---

## Month-Only Orders Must Use Rotation Scheduling; Week-Column Orders Stay Default

**Session:** Universal rule (2026-05-14)

**Rule:** Scheduling type is determined by how the IO/order is structured:

- **Week columns present** (order lists spots per week) → leave scheduling type at default (Priority, type 0). Pass `spots_per_week > 0` to `add_contract_line()`.
- **Month column only** (no weekly breakdown, just a total per month) → **Rotation** (type 1). Pass `spots_per_week=0` with the full-month date range.

`EtereClient.add_contract_line()` enforces this automatically: any line where `spots_per_week == 0` AND the flight is longer than 7 days is flagged `_is_monthly = True` and Rotation is selected. **No extra flag needed from the automation.**

---

## All Parsers Must Set `rates_are_net` on Their Order Object

**Session:** Backwrite gross-up automation (2026-04-17)

**Rule:** Every parser's order dataclass must carry a `rates_are_net: bool` field.

- `False` (default) — rates in the IO are Gross; no gross-up needed
- `True` — rates in the IO are Net; backwrite will auto-gross-up by dividing by `(1 - agency_fee)`

**How to apply:** When writing a new parser, add `rates_are_net: bool = False` to the order dataclass. If the format is always net, set it `True`. If it depends on a column header, detect it: `bool(re.search(r'\bNet\b', header) and not re.search(r'\bGross\b', header))`.

---

## Master Market Is Always NYC — Never Override It in Agency Automations

**Session:** SCWA implementation (2026-03-26)

**Rule:** Master market is set ONCE by `EtereSession` before any automation runs. Agency automation files must NEVER call `etere.set_master_market()` — doing so fires a second market-selection and can set the wrong market.

**Universal defaults:**
- Master market = **NYC** for ALL orders — Crossings TV and any other agency
- Master market = **DAL** only for WorldLink / The Asian Channel

When writing a new agency automation, do NOT include a `set_master_market` call. The session handles it. The line-level `market` argument to `add_contract_line()` is separate and correct to set per line.
