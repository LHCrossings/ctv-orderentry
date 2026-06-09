# Lessons Learned

## New Parser Checklist for Direct DB (All Future Parsers Are Direct DB)

**Session:** Pink-pill testing sweep (2026-06-09)

**Rule:** We no longer write Selenium order-entry parsers. Every new parser is direct DB. When building one, apply ALL of the following from the start — these were all discovered as bugs during the 2026-06 testing sweep:

### 1. Duration: always pass `str(seconds)`, never `f":{sec:02d}"`
`_duration_str_to_seconds()` in `etere_direct_client.py` splits on `:` — a leading colon (e.g. `":30"`) produces `['', '30']` and `int('')` crashes. Pass bare integer strings: `str(spot_duration)` (e.g. `"30"`, `"45"`).

### 2. `contracts` list must be populated on success; use gathered code, not DB ID
`ProcessingResult.contracts` must contain at least one `Contract(contract_number=order_code, order_type=OrderType.X)` when `success=True`. Never return `contracts=[]` on success — the final summary will show "0 contracts created" even if Etere has the data.

Use the **gathered contract code** (from `user_input.get('contract_code')`), not the Etere DB integer ID. The DB ID is meaningless to the user in the summary; the code they typed is what they need to verify the entry. Pattern:
```python
inp = order.order_input
label = (inp.get('contract_code') if isinstance(inp, dict) else None) or str(contract_id)
contracts = [Contract(contract_number=label, order_type=OrderType.X)] if success else []
```
For multi-contract types (SACCOUNTYVOTERS), build one `Contract` per phase from `inp.get('phase1_inputs', {}).get('contract_code')` etc.

### 3. `booking_code` must always be explicit — never rely on `is_bonus`
Pass `booking_code=10 if is_bonus else 2` to every `add_contract_line()` call. `is_bonus=True` only sets the scheduling type; it does NOT set the booking code.

### 4. Customer ID must be resolved in `gather_*_inputs()`, not during processing
All user-interactive prompts (customer ID, order code, description) belong in the upfront gather function registered in `_INPUT_GATHERERS`. If `_resolve_customer_id()` or any `input()` call fires during processing, move it to gather.

### 5. `gather_*_inputs` must return a dict; service uses `user_input.get('key')`
Service methods check `isinstance(inp, dict)` and use `.get('order_code')` / `.get('contract_code')`. The gathered dict must use the key `'order_code'` (or `'contract_code'`) so the contracts-list builder can find it.

### 6. Yes/Enter at a date-override prompt must keep the original date
Pattern: `actual = raw if raw and raw.lower() not in ('y', 'yes') else original`. Never do `actual = raw if raw else original` — typing "yes" stores the string "yes" as the date.

### 7. `_DIRECT_DB_TESTED_KEYS` and service/bridge registration — 3 files
After writing and testing a new parser:
1. `_DIRECT_DB_ORDER_TYPES` in `order_processing_service.py`
2. `_DIRECT_DB_KEYS` in `parser_bridge.py` (white pill → no pill until tested)
3. `_DIRECT_DB_TESTED_KEYS` in `parser_bridge.py` (add after passing pink-pill test)
Missing step 3 leaves the pill pink indefinitely. Missing step 1 causes a browser session to be opened.

### 8. `gather_*_inputs` must prompt for contract code and description
Every gather function must ask the user for the contract code and description before processing starts. Never let the processing function prompt for these or auto-generate them silently.

For multi-contract types, the pattern varies:
- **Single contract**: return `{'order_code': code, 'description': desc, ...}`
- **Multi-estimate (TCAA)**: ask for a shared prefix; service builds `f"{prefix} {est_num}"` per estimate; return `{'order_code_prefix': prefix, 'description': desc, ...}`
- **Per-market (Polaris)**: gather a dict `contracts[market] = {'code': ..., 'description': ...}` for each market
- **Per-quarter (Impact, Lexus)**: each element in `quarter_inputs` list includes `'order_code'` and `'description'`

The service's `_process_*_order` must read these from `user_input` and pass them to the automation function. Never fall back to auto-generation when the user has already provided a value.

---

## `parse_day_bits` (DirectDB) and `_select_days` (Selenium) Must Stay in Sync

**Session:** Admerasia DirectDB conversion (2026-06-08)

**Rule:** There are two day-parsing implementations in this codebase:
- `EtereClient._select_days()` — Selenium path (`etere_client.py` ~line 1262). The **original** reference. Supports M/T/W/R/F/S/U single-char, ranges (M-F, Sa-Su), and comma lists.
- `parse_day_bits()` — DirectDB path (`etere_direct_client.py` ~line 361). Must support **all the same aliases**.

When Admerasia was converted to DirectDB, `T` (Tuesday), `S` (Saturday), and `U` (Sunday) — single-char codes used by the Admerasia parser — were missing from `parse_day_bits`. The result: lines were created with all day flags = False, so the scheduler could never place any spots.

**How to apply:**
1. Any time you add a day alias to `_select_days` in `etere_client.py`, also add it to `_TOKEN_MAP` and `_TOKEN_TO_INDEX` in `etere_direct_client.py`.
2. Any time you convert a parser to DirectDB, run a quick sanity-check: print the `days` string each line will pass to `add_contract_line` and confirm `parse_day_bits` produces at least one `True` flag for each. A line where all flags are False will silently enter but never schedule.
3. The canonical full alias set is in `_select_days` — treat it as the source of truth when syncing.

**Known full single-char set:** M=Monday, T=Tuesday, W=Wednesday, R=Thursday, F=Friday, S=Saturday, U=Sunday.

## Language-Targeted Traffic Instructions Must Use Day/Time Window Filters — Never Line Description Matching

**Session:** RPM Thunder Valley (2026-06-02)

**Rule:** Any traffic instruction format that assigns spots per language (Cantonese, Mandarin, Vietnamese, etc.) must use `_CTV_LANG_WINDOWS` (or `_DAL_LANG_WINDOWS` for The Asian Channel) time-window filters — exactly as H&L Partners and RPM do. Never attempt to detect language by matching against contract line descriptions (e.g. checking that a line starts with "C " or "M ").

**Why:** Line descriptions are free-text and change. Time windows are the ground truth: a spot that airs Monday 19:00–20:00 is Cantonese because that's what Crossings TV programs in that slot. Line description matching will silently mis-assign when descriptions are renamed or formatted differently.

**How to apply:**
1. Extract `system_dialect` from the spot title (keyword match: "cantonese" → `"Cantonese"`, etc.)
2. Build `filters_dict = {"languages": [sys_dialect], "duration": dur_sec}` (plus `date_from`/`date_to`)
3. Count scheduled spots via `_build_spot_filter(filters_dict)` against `TPALINSE`
4. Assign via `/api/traffic/contract/{id}/assign` with `spots=[{id: filmati_id, ...}]` and `filters=filters_dict`
5. Route the response through the H&L grid (`renderHLGrid` / `applyHLTraffic`) — shape the backend response with `contracts → dialect_assignments` identical to the H&L format

The `/assign` endpoint handles the time-window SQL internally. Never replicate that logic in the parser or by inspecting line descriptions.

---

## assign_blocks_for_existing_line Strips All Asterisks From DESCRIZIONE

**Session:** Make Goods (2026-05-29)

**What happened:** Used `*MG*` as a prefix for make-good line descriptions. After `assign_blocks_for_existing_line` ran, the asterisks were silently stripped. The description landed as `MG M-F ...` instead of `*MG* M-F ...`.

**Rule:** `assign_blocks_for_existing_line` runs this SQL after every block refresh:
```sql
UPDATE CONTRATTIRIGHE
SET    DESCRIZIONE = RTRIM(REPLACE(DESCRIZIONE, '*', ''))
WHERE  ID_CONTRATTIRIGHE = %s AND DESCRIZIONE LIKE '%*%'
```
This strips ALL asterisks from any description that contains one — by design, to clean up trailing `*` Etere appends after block operations.

**How to apply:** Never use `*` in programmatically-set description prefixes on lines that go through block refresh. Use hyphens or brackets instead: `-MG-`, `[MG]`, etc.

---

## Make-Good Lines Must Use row_status=2 (Change Data), Not row_status=1

**Session:** Make Goods (2026-05-29)

**What happened:** Initially set `row_status=1` for make-good lines. This sent them directly to the scheduler. The correct value is `row_status=2` (Change Data), which holds the line pending approval before scheduling runs.

**Rule:** From `add_contract_line` signature: `row_status: int = 0 — 0=Ready, 2=Change Data`. Use:
- `row_status=0` — line is ready, scheduler picks it up immediately
- `row_status=2` — line requires approval (Change Data gate) before scheduling

**How to apply:** Any line inserted as a revision, make-good, or correction to an already-approved contract must use `row_status=2`. Only use `row_status=0` for fresh new lines on contracts that haven't been approved yet.

---

## Showing/Hiding `<tr>` Elements in JavaScript Requires `display='table-row'`

**Session:** Make Goods (2026-05-29)

**What happened:** Used `element.style.display = ''` to show a hidden `<tr>`. This removes the inline style but falls back to the CSS `display: none` — the row stays invisible. Nothing happened when the MG button was clicked.

**Rule:** To show a `<tr>` that has `display: none` in CSS, set `element.style.display = 'table-row'` explicitly. Setting it to `''` only works if the element has no CSS rule hiding it.

```js
// Wrong — reverts to CSS display:none
row.style.display = '';

// Correct
row.style.display = 'table-row';
```

**How to apply:** Any time a `<tr>` is toggled visible in JS, use `'table-row'`. For `<div>`, use `'block'` or `'flex'`. Never use `''` to unhide an element that has a CSS `display:none` rule.

---

## Time Advertising "Thematic" Is a Creative Title, Not a Paid/Bonus Indicator

**Session:** Time Advertising direct DB conversion (2026-05-29)

**Rule:** In Time Advertising broadcast orders, "Thematic" (or "Thematic … Existing") is the **title of the ad creative** to air — a traffic instruction to use the spot called "Thematic." It has nothing to do with whether the line is paid or bonus.

Paid vs. bonus is determined exclusively by whether the line has a **rate**:
- Rate > 0 → Paid Commercial (`booking_code=2`)
- Rate = 0 → BNS (`booking_code=10`)

**Do NOT** use `is_thematic` or section header keywords ("thematic", "free") to infer booking code. The `is_thematic` flag in `TimeAdvertisingLine` is a parser artifact (section header detection) and is unreliable. The automation correctly ignores it and uses `ln.rate == 0` exclusively.

**Applies to:** `timeadvertising_automation.py` and any future Time Advertising parser work.

---

## Etere Blacklist — Complete Reference

**Sessions:** Missing Materials blacklist button (2026-05-29) + Make Goods reconciliation (2026-05-29)

---

### The Accounting Formula

For any contract line, at all times:

```
N_PASSAGGI  =  trafficPalinse rows  +  TSL.PassageMiss
(ordered)       (placed/aired)          (blacklisted)
```

If `trafficPalinse + TSL.PassageMiss < N_PASSAGGI`, there are **orphaned deletions** — spots that were removed from the schedule but whose blacklist count was never written. These appear as phantom "remaining" spots that will never air and never make-good.

**Source of truth for what actually aired:** `trafficPalinse`. Not TPALINSE (pre-air schedule), not N_PASSAGGI (ordered count). Use `trafficPalinse` joined to `TPALINSE` for the air date and time.

---

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

- `ID_TRAFFICPALINSE` = `trafficPalinse.id_trafficPalinse` of the deleted row
- `Date` / `ToDate` = **always** `CONTRATTIRIGHE.DATA_INIZIO` / `DATA_FINE` — **never leave NULL**

---

### Critical: PassageMiss Must Always Be Incremented

❌ **Wrong (original implementation):** Check `COUNT(*) == 0` → INSERT; else skip entirely.
This silently orphans every spot after the first: TPALINSE/trafficPalinse rows are deleted but PassageMiss never increases. The spots vanish from the schedule AND from blacklist accounting.

✅ **Correct:** INSERT on first occurrence; `PassageMiss + 1` on every subsequent spot for that line.

---

### Critical: TSL Date/ToDate Must Never Be NULL

If `Date`/`ToDate` on a TSL row are NULL, that blacklisted spot will be **invisible in every date-range query** (SQL `NULL <= date` evaluates to NULL/false). Always read `DATA_INIZIO`/`DATA_FINE` from `CONTRATTIRIGHE` before inserting.

In any query filtering by date range, use:
```sql
ISNULL(tsl.Date,   cr.DATA_INIZIO) <= @date_to
ISNULL(tsl.ToDate, cr.DATA_FINE)   >= @date_from
```

---

### What NOT to Do

- ❌ `LIVELLO=666` alone — Etere counts 666 rows AND TSL separately → doubles blacklist count
- ❌ TSL-only (leaving LIVELLO=0) → trafficPalinse count stays high, placed + blacklisted > N_PASSAGGI
- ❌ Skipping TSL write if entry already exists → orphaned deletions (the bug above)
- ❌ Leaving TSL Date/ToDate NULL → spot disappears from all date range reports

---

### Detecting Orphaned Deletions

Run this on any contract to find lines where the formula breaks:

```sql
SELECT cr.ID_CONTRATTIRIGHE, cr.DESCRIZIONE,
       cr.N_PASSAGGI AS ordered,
       ISNULL(tp.placed, 0) AS placed,
       ISNULL(tsl.missed, 0) AS blacklisted,
       cr.N_PASSAGGI - ISNULL(tp.placed, 0) - ISNULL(tsl.missed, 0) AS orphaned
FROM CONTRATTIRIGHE cr
LEFT JOIN (SELECT ID_ContrattiRighe, COUNT(*) AS placed
           FROM trafficPalinse GROUP BY ID_ContrattiRighe) tp
    ON tp.ID_ContrattiRighe = cr.ID_CONTRATTIRIGHE
LEFT JOIN (SELECT ID_ContrattiRighe, SUM(PassageMiss) AS missed
           FROM Traffic_ScheduleList WHERE BlackList > 0
           GROUP BY ID_ContrattiRighe) tsl
    ON tsl.ID_ContrattiRighe = cr.ID_CONTRATTIRIGHE
WHERE cr.ID_CONTRATTITESTATA = @contract_id
  AND cr.N_PASSAGGI - ISNULL(tp.placed,0) - ISNULL(tsl.missed,0) > 0
```

Fix: `UPDATE Traffic_ScheduleList SET PassageMiss = PassageMiss + @orphaned WHERE ID_ContrattiRighe = @line_id AND BlackList > 0`. If no TSL row exists yet, INSERT with `PassageMiss = @orphaned`.

---

### Also Confirmed

- `tcFrames2Msec(COD_USER, ORA)` crashes if COD_USER is a numeric market ID (e.g. 4=SFO) — it expects a VideoStandard char. Convert frames manually in Python: `total_sec = ora // fps; hh = total_sec // 3600` etc.
- `pymssql` requires explicit `conn.commit()` — `with conn:` does NOT auto-commit
- TPALINSE has triggers → `OUTPUT INSERTED.x` is blocked; use `SELECT SCOPE_IDENTITY()` after INSERT

## Every Traffic Assignment Must Populate CONTRATTIFILMATI (the "Rotate with the following assets" pool)

**Session:** Tatari/MA Woof/Pholicious fix (2026-05-28)

**What happened:** Tatari drag-and-drop correctly assigned filmati to individual TPALINSE spots, but the "Rotate with the following assets" pool in Etere's native UI (table: `CONTRATTIFILMATI`) was empty for new contract lines added after the first assignment run. The `existing_pool` check saw the filmati were already in other lines' pool and skipped `MaterialAddToAssetListC`, so new lines never got their pool rows created.

**Rule:** Any code that assigns traffic (via `auto-assign`, `assign`, or `assign-spots`) **must** ensure `CONTRATTIFILMATI` is populated for every assigned line. Two requirements:

1. **`MaterialAddToAssetListC`** — the Etere HTTP call that registers filmati in the contract pool. Must be called for each filmati not yet in the pool for the specific lines being assigned (not just "anywhere in the contract"). Endpoint: `POST /Sales/MaterialAddToAssetListC` with `{"idFilmatiList": [fid], "idct": contract_id}`.

2. **`CONTRATTIFILMATI` rows** — one row per `(ID_CONTRATTIRIGHE, ID_FILMATI)` with `PERCROTATION = 0`. Use DELETE+INSERT (not UPDATE+INSERT-if-rowcount) so new lines that were added after the HTTP call ran still get their rows. **Do NOT calculate or set PERCROTATION** — the actual rotation percentages are set separately via the portal's rotation builder or manually in Etere. Setting per-line proportional values causes each filmati to appear multiple times in Etere's "rotate by order" view (which deduplicates by `filmati + PERCROTATION`). The pool rows just need to exist; actual rotation is driven by `TPALINSE.ID_FILMATI`.

3. **Cleanup DELETE must EXCLUDE assigned lines.** `MaterialAddToAssetListC` adds `PERCROTATION=0` rows to every line in the contract. The cleanup that removes rows for non-assigned lines MUST include `AND ID_CONTRATTIRIGHE NOT IN ({assigned_line_ids})` — otherwise it deletes the rows that were just inserted for the assigned lines, leaving the pool empty. This is a repeatedly-seen bug: the pool looks empty even though TPALINSE has correct spot assignments.

**How to apply:** This is a **mandatory checklist item for every new traffic instruction format**, not just new endpoints. Any time a new parser/format is wired into `parse-instructions` (e.g. RPM, a new agency), verify before shipping:

1. The assignment path it uses (`/assign`, `/auto-assign`, or `/assign-spots`) calls `MaterialAddToAssetListC` for each filmati being registered.
2. `CONTRATTIFILMATI` rows are written (DELETE+INSERT) for every `(ID_CONTRATTIRIGHE, ID_FILMATI)` pair on the lines being assigned.

Even when reusing an existing endpoint (e.g. RPM reuses `/assign` same as H&L), the new filmati/contract combination may not yet be in the pool — the pool check is per-line, not per-contract. TPALINSE being correct is necessary but not sufficient — Etere's native UI will show the pool as empty if CONTRATTIFILMATI isn't populated, confusing traffic managers.

---

## Direct DB Entry Must Always Pass `booking_code` Explicitly

**Session:** iGraphix + WorldLink BNS fix (2026-05-28)

**What happened:** `is_bonus=True` in `EtereDirectClient.add_contract_line()` only controls the scheduling type (forces Rotation) and the NEWTYPE string. It does NOT override the `booking_code` parameter, which defaults to `2` (Paid Commercial). Bonus lines were entered with booking code 2 instead of 10 (BNS).

**Rule:** Any direct DB automation call to `add_contract_line()` must always pass `booking_code` explicitly:
```python
booking_code=10 if is_bonus else 2
```

Never rely on `is_bonus=True` to set the booking code automatically. The Selenium `EtereClient` path uses a `spot_code` variable for this — replicate that pattern in every direct DB conversion.

**Applies to:** Every future direct DB conversion. When converting a Selenium automation, find where it computes `spot_code = 10 if is_bonus else 2` and carry that logic into the direct DB call as `booking_code=spot_code`.

---

## EtereDirectClient SP Calls Must Use `self._ph`, Not Hardcoded `?`

**Session:** Trade entry direct DB write (2026-05-21)

**What happened:** `web_sales_savecontractgeneral` and `web_sales_InsertContractLine` SQL strings were written with hardcoded `?` placeholders. pymssql requires `%s`. The `_ph` attribute is set correctly on the client (`'%s'` for pymssql, `'?'` for pyodbc) but was only used in ad-hoc queries — not in the SP call strings. Result: `Incorrect syntax near '?'` at runtime.

**Rule:** Any SQL string in `etere_direct_client.py` that contains `?` placeholders must be executed as `cursor.execute(sql.replace('?', self._ph), params)`. Never hardcode `?` and call `.execute(sql, params)` directly — it will break on pymssql connections.

**Applies to:** Both SP calls in `etere_direct_client.py` (header + line inserts), and any future SP calls added to the file.

## Month-Only Orders Must Use Rotation Scheduling; Week-Column Orders Stay Default

**Session:** Universal rule (2026-05-14)

**Rule:** Scheduling type is determined by how the IO/order is structured:

- **Week columns present** (order lists spots per week) → leave scheduling type at default (Priority, type 0). Pass `spots_per_week > 0` to `add_contract_line()`.
- **Month column only** (no weekly breakdown, just a total per month) → **Rotation** (type 1). Pass `spots_per_week=0` with the full-month date range.

`EtereClient.add_contract_line()` enforces this automatically: any line where `spots_per_week == 0` AND the flight is longer than 7 days is flagged `_is_monthly = True` and Rotation is selected (etere_client.py ~line 1023). **No extra flag needed from the automation.**

**Applies to:** Any new parser handling a monthly-structure IO (e.g. RWNY). Set `spots_per_week=0` and pass the full-month start/end dates — Rotation fires automatically. Do NOT pass `spots_per_week > 0` for monthly orders or the auto-Rotation will be suppressed.

**Max weekly cap:** For monthly orders, `contractLineGeneralMaxWeekSchedule` must also be 0. `EtereClient` enforces this automatically: if `_is_monthly` is True and `spots_per_week` was passed non-zero, it is clamped to 0 before reaching the form field (`etere_client.py`, just after `_force_rotation` assignment).

---

## Time Suffix Inheritance Must Never Produce a Midnight-Crossing Range

**Session:** Lexus EST (2026-05-11)

**What happened:** Program name `Ss 1130-12N Vt Variety` parsed as `23:30–12:00`. Start `1130` had no suffix, so it inherited `PM` from end `12N` (noon→PM). Result: 11:30 PM to noon — crosses midnight.

**Rule:** A valid daypart never crosses midnight. After inheriting the end suffix, compare start vs. end in 24-hour minutes. If `start_minutes > end_minutes`, the inherited suffix is wrong — flip it (PM→AM or AM→PM). Only flip when the suffix was inferred (no explicit suffix on start); never override an explicit user-written suffix.

```python
if not start_sfx_raw:
    if _to_mins(start_h, start_sfx) > _to_mins(end_h, end_sfx):
        start_sfx = 'AM' if start_sfx == 'PM' else 'PM'
```

**Applies to:** `_extract_time_from_program` in `lexus_parser.py`, and any future parser that infers AM/PM from context. The midnight-crossing test is the universal correctness check.

## All Parsers Must Set `rates_are_net` on Their Order Object

**Session:** Backwrite gross-up automation (2026-04-17)

**Rule:** Every parser's order dataclass must carry a `rates_are_net: bool` field.

- `False` (default) — rates in the IO are Gross; no gross-up needed
- `True` — rates in the IO are Net; backwrite will auto-gross-up by dividing by `(1 - agency_fee)`

**Current state:**
- Admerasia: hardcoded `rates_are_net = True` (Admerasia IOs are always net)
- HL: detected per-file from column header (`"Net"` present, `"Gross"` absent)
- All others: should default to `False` unless detection logic is added

**How the system uses it:**
1. `parser_bridge.get_order_detail()` propagates the flag as `"rates_are_net"` in its return dict
2. `/backwrite/parse-io` endpoint returns `{rates_are_net, io_net_rates}` so the JS can pre-fill the gross-up table
3. If the user uploads an IO before clicking Generate, gross-up inputs are auto-checked and pre-filled
4. Server-side fallback in `/generate`: if `io_detail.rates_are_net` and Agency order and no manual gross_up, auto-injects `gross_up_rates = {net_rate: net_rate}` for each IO rate

**How to apply:** When writing a new parser, add `rates_are_net: bool = False` to the order dataclass. If the format is always net, set it `True`. If it depends on a column header, detect it the same way HL does (`bool(re.search(r'\bNet\b', header) and not re.search(r'\bGross\b', header))`).



## Master Market Is Always NYC — Never Override It in Agency Automations

**Session:** SCWA implementation (2026-03-26)

**Rule:** Master market is set ONCE by `EtereSession` before any automation runs. Agency automation files must NEVER call `etere.set_master_market()` — doing so fires a second market-selection and can set the wrong market.

The only valid exception: **WorldLink** (The Asian Channel) orders, which use master market **DAL** (Dallas).

**Universal defaults:**
- Master market = **NYC** for ALL orders — Crossings TV and any other agency
- Master market = **DAL** only for WorldLink / The Asian Channel

**How to apply:** When writing a new agency automation, do NOT include a `set_master_market` call. The session handles it. The line-level `market` argument to `add_contract_line()` is separate and correct to set per line.

## Bookend Orders: Halve Spot Counts, Double Rate Before Etere Entry

**Session:** Imprenta PG&E bookend fix (2026-03-16)

**Rule:** When an order is a bookend order, Etere's "Top and Bottom" scheduling fires **2 spots per line entry** — one at the top of the break and one at the bottom. Entering the PDF spot count directly would double the spots on air.

**Fix (universal — applies to Imprenta, Impact, and any future bookend order):**
- `spots_per_week` ÷ 2
- `total_spots` ÷ 2
- `rate` × 2 (paid lines only — bonus lines stay at $0)
- Halving applies to **all** lines in a bookend order, including bonus lines (they also air top+bottom)
- If any bookend line has an odd spot count, **abort with an error** — bookends must run in pairs and the AE must correct the order before entry.

**Key distinction:** Use the order-level `is_bookend` flag (e.g. `parse_result.is_bookend`) for BOTH the spot-halving condition AND the `is_bookend` key passed to Etere — not the line-level flag (which is False for bonus lines). Using `line.is_bookend` for the Etere scheduling flag causes bonus lines to enter as rotation instead of Top/Bottom.

## Melissa Uses "Ss" to Mean Saturday+Sunday

**Session:** Lexus EST 210 (2026-03-13)

**What happened:** Program name `Ss 12N-1P Vt Drama` had `Ss` as the day token. The tokenizer
matched `S` (Saturday) and dropped the trailing `s`, producing Saturday-only instead of Sa-Su.

**Rule:** `Ss` (mixed case, exactly 2 characters) = Melissa's shorthand for Saturday+Sunday.
Normalise to `Sa-Su` before tokenizing in `_extract_days_from_program`. Check for similar
shorthand variants if new notation appears.

## 12N (Noon) Must Be Handled in All Time Extraction Regexes

**Session:** Lexus EST 207 / EST 210 (2026-03-13)

**What happened:** Program names like `M-Su 1130A-12N VT Variety` and `M-Su 12N-1P Vt Drama`
were entered as `06:00–23:59` (the empty-time fallback). The time extraction regex only
included `[AaPpMm]` as valid suffix characters — `N` (noon) was not in the set, so `12N`
failed to match entirely.

**Rule:** ANY time extraction regex that accepts `A`, `P`, or `M` as a suffix MUST also accept
`N`/`n` for noon. The `_normalise_suffix` function must map `N`/`NOON` → `'PM'` (noon = 12 PM).
Be **extremely careful** with `11:30A-12N`, `12N-1P`, and any range ending or starting at noon.
The silent fallback to `06:00–23:59` means the error will pass validation with no warning.

**Applies to:** `lexus_parser.py`, `imprenta_parser.py`, and any future parser with embedded
time extraction logic.

## Tests Are Not Authoritative for String Constants

**Session:** Market code mismatch fix (2026-02-19)

**What happened:** `test_chinese_block_abbreviation` expected `"C/M"` but
`Language.MANDARIN.get_block_abbreviation()` returned `"M/C"`. The test was stale — the code
was correct. Correctly flagged this as a pre-existing failure (not introduced by the change),
then fixed the test when user confirmed `"M/C"` is correct.

**Rule:** When a test and implementation disagree on a string constant, do NOT silently fix
either side. Surface the conflict explicitly, state which side you believe is correct and why,
and let the user confirm before touching anything.

## OCR Parser Failures Are Silent by Default — Always Verify Spot/Line Counts

**Session:** RPM Muckleshoot 10868 (2026-02-24)

**What happened:** RPM parser silently dropped 3 of 8 lines (37% of spots, $1,932). No error
was raised — the parser just returned fewer lines. Two distinct OCR artifact patterns:

1. **Space in time range:** `6:00a- 8:00p` → column shift → rate field received `RT` → Decimal
   parse failed → line silently skipped. Fix: preprocess `(\d+:\d+[ap])-\s+(\d+:\d+[ap])` → join.

2. **Doubled letter in day code:** `MTuWTHhF` (OCR doubled the `h` in `Th`) → exact-match regex
   `MTuWThF` didn't match → line skipped. Fix: use `MT[A-Za-z]+F` pattern everywhere the day
   code is matched or parsed.

**Rule:** After any RPM parser change, run `parse_rpm_pdf` on the PDF and verify:
- Line count matches the PDF's line count
- Total spots match the PDF's "Total Spots" footer
- Total cost matches the PDF's "Total Cost" footer
Never trust "parsed successfully" without checking the numbers.

## Image-Based PDFs Have Structural Variants — Min-Column Guards Must Be Dynamic

**Session:** Misfit Supplemental Budget (2026-02-24)

**What happened:** Misfit parser used `len(row) < 10` to skip short rows. Supplemental budget
PDFs cover only 3 weeks → 8 columns → ALL data rows skipped silently. Additionally the header
Market cell was Python None (not a string), producing `order.markets = ['None']` which matched
no parsed lines. 0 lines entered despite a valid contract being created.

**Rule:** Column count guards should reflect the minimum structure (≥5 for Misfit tables), not
the typical case. Always derive `markets` from parsed `line.market` values, not the header field
which can be absent in supplemental/non-standard PDFs.

## Static Day-Pattern Dict in _select_days Silently Defaults to All Days

**Session:** Admerasia McDonald's SEA 11-MD10-2603CT (2026-02-25)

**What happened:** `_select_days` used a hardcoded dict mapping known strings to checkbox
indices. Any unrecognised string (M,W,R,F / M-R / M,R / S / U) silently fell through to the
default `[0,1,2,3,4,5,6]` = M-Su. Result: every Admerasia line got all 7 days checked.

**Rule:** Never use a static dict + silent all-day default for day-pattern parsing. Use a
proper parser (`_parse_day_codes`) that handles ranges, comma lists, and single codes, and
**warns explicitly** on unknown input rather than defaulting silently. Verify by running
`_parse_day_codes` against every pattern a parser can produce before shipping.

## Admerasia Day Selection Must Come From Calendar Grid, Not Program Bracket

**Session:** Admerasia McDonald's SEA 11-MD10-2603CT (2026-02-25)

**What happened:** Misread user complaint about day selection. Thought the fix was to use
the program name bracket `(M-F)` as the Etere day string. This was wrong — it caused Etere
to freely distribute 10 spots across 15 available M-F slots instead of placing them on the
exact days specified in the calendar grid.

Admerasia orders are ordered **day by day**. Each cell in the calendar grid specifies the
exact number of spots for that exact date. The Etere day selection must reflect precisely
which days have spots (and per_day_max must match the count in the cell).

**Rule:** Never use the program name bracket to override calendar-derived day strings for
Admerasia. The bracket describes when the program airs; the calendar grid is the purchase
order. Use the grid to build exact per-week Etere lines with precise day patterns and
per_day_max values.

## Admerasia Chinese Format Detection Must Check Col 0, Not Just Col 1

**Session:** Admerasia McDonald's SEA 11-MD10-2603CT (2026-02-25)

**What happened:** Parser detected "Vietnamese format" for a Chinese-language order, producing
0 lines. The Vietnamese/Chinese format detection checked `first_data_row[1]` for `:\d+s?` (spot
length). In this order the spot length (`:15`) was in col 0 and an ad title text was in col 1
(`ACM Yes/ACM Name/`), so the check failed and col offsets were set to Vietnamese mode
(`program_col=2`). Column 2 is `None` for all data rows → every line skipped silently.

Also found: PDF typo `10:300p` (3-digit minute). The normalizer's pre-process regex
`re.sub(r'(\d+):(\d{2})\d+([ap])', ...)` trims extra minute digits before pattern matching.

**Rules:**
1. Chinese format detection must check **both col 0 and col 1** for the `:\d+s?` spot length
   pattern — the column position varies across orders.
2. When 0 lines are found, immediately dump the raw table rows around `row_offset` to identify
   which skip condition is firing (no program, no rate, garbled time, etc.).
3. Add the `10:300p`-style 3-digit minute sanitization as a pre-process step in
   `_normalize_time_to_colon_format` to handle PDF OCR/typo artifacts silently.
