# Lessons Learned

## Etere Blacklist Is a DELETE + TSL Insert, Not a LIVELLO Change

**Session:** Missing Materials blacklist button (2026-05-29)

**What happened:** Built a "Blacklist" button on the missing materials page. Went through several wrong approaches before confirming the correct mechanism by watching the DB during a live native Etere blacklist action:

- ❌ `LIVELLO=666` alone — Etere counts 666 rows AND TSL separately → doubles the blacklist count
- ❌ TSL-only (leaving LIVELLO=0) → placed count stays at N_PASSAGGI, so placed + blacklisted > N_PASSAGGI
- ✅ **DELETE the TPALINSE row + DELETE the trafficPalinse row + INSERT TSL** — this is exactly what Etere does natively

**Rule:** To blacklist a scheduled spot programmatically:
1. `DELETE FROM trafficPalinse WHERE id_tpalinse = %s`
2. `DELETE FROM TPALINSE WHERE ID_TPALINSE = %s`
3. `INSERT INTO Traffic_ScheduleList (ID_ContrattiRighe, BlackList, PassageMiss, ID_TRAFFICPALINSE, Date, ToDate, Notes, Operator, ID_FILMATI, ID_FILMATI_TAIL, ID_FILMATI_MIDDLE, ID_FATTURAEMITTENTE, Split) VALUES (%s, 1, 1, %s, %s, %s, %s, %s, -1, -1, -1, 0, 0)`
   - `ID_TRAFFICPALINSE` = the `trafficPalinse.id_trafficPalinse` of the deleted row
   - `Date` / `ToDate` = `CONTRATTIRIGHE.DATA_INIZIO` / `DATA_FINE` for that contract line
   - INSERT only if no existing `BlackList > 0` entry for that line (never increment)

**Result:** N_PASSAGGI = placed (LIVELLO=0) + blacklisted (TSL.PassageMiss). Etere contract view shows correct 10/9/1.

**Also confirmed:**
- TPALINSE rows with invalid ORA values will crash `tcFrames2Msec` — filter by ORA range directly instead of calling the SP in WHERE
- `pymssql` requires explicit `conn.commit()` — the `with conn:` context manager does NOT auto-commit in this project's connection wrapper
- TPALINSE has triggers → `OUTPUT INSERTED.x` is blocked; use `SELECT SCOPE_IDENTITY()` after INSERT instead

## Every Traffic Assignment Must Populate CONTRATTIFILMATI (the "Rotate with the following assets" pool)

**Session:** Tatari/MA Woof/Pholicious fix (2026-05-28)

**What happened:** Tatari drag-and-drop correctly assigned filmati to individual TPALINSE spots, but the "Rotate with the following assets" pool in Etere's native UI (table: `CONTRATTIFILMATI`) was empty for new contract lines added after the first assignment run. The `existing_pool` check saw the filmati were already in other lines' pool and skipped `MaterialAddToAssetListC`, so new lines never got their pool rows created.

**Rule:** Any code that assigns traffic (via `auto-assign`, `assign`, or `assign-spots`) **must** ensure `CONTRATTIFILMATI` is populated for every assigned line. Two requirements:

1. **`MaterialAddToAssetListC`** — the Etere HTTP call that registers filmati in the contract pool. Must be called for each filmati not yet in the pool for the specific lines being assigned (not just "anywhere in the contract"). Endpoint: `POST /Sales/MaterialAddToAssetListC` with `{"idFilmatiList": [fid], "idct": contract_id}`.

2. **`CONTRATTIFILMATI` rows** — one row per `(ID_CONTRATTIRIGHE, ID_FILMATI)` with `PERCROTATION = 0`. Use DELETE+INSERT (not UPDATE+INSERT-if-rowcount) so new lines that were added after the HTTP call ran still get their rows. **Do NOT calculate or set PERCROTATION** — the actual rotation percentages are set separately via the portal's rotation builder or manually in Etere. Setting per-line proportional values causes each filmati to appear multiple times in Etere's "rotate by order" view (which deduplicates by `filmati + PERCROTATION`). The pool rows just need to exist; actual rotation is driven by `TPALINSE.ID_FILMATI`.

**How to apply:** When writing any new traffic assignment endpoint or modifying existing ones, confirm both of the above are handled. TPALINSE being correct is necessary but not sufficient — Etere's native UI will show the pool as empty if CONTRATTIFILMATI isn't populated, confusing traffic managers.

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
