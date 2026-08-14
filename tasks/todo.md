# Ntooitive Parser (LA Care / CRC 2026 REV 2)

## What the format is

"Crossings TV Media Proposal" — Charmaine's house template, sent by agency
**Ntooitive** (Laura Searcy) for advertiser **L.A. Care- CRC**. Weekly-column
grid, closest existing reader: `parse_crispin_xlsx` (same family), but with
different column labels and **GROSS** money:

- Columns: `Language Block | Day Part/Program | Spot Type | Length | <week
  dates as real datetimes> | Total Unit # | Promo Unit Cost (Gross) | Line
  Total Cost (Gross) | Line Total Cost (NET)`
- Paid rows: Spot Type `COM`; bonus rows: col A + Spot Type `BONUS`, daypart
  `<Lang> ROS`
- Footers: `Total Paid` / `Total Bonuses` (units + gross + net)
- Header block: Agency, Advertiser, Flight schedule, Market, Billing Cycle,
  `Gross (Airtime)`, `Gross Translation Fees` (→ production charge),
  `Gross Amount of Contract`
- **Rates are GROSS** — NET column = gross × 0.85. ANAGRAF nets down
  (Ntooitive = agency 299, Commissione 15.00). `rates_are_net = False`.
  NEVER multiply in the parser (Crispin lesson).
- Workbook carries multiple **Option sheets** (Option 1 = REVISED 8/14,
  flight 8/18–11/30 — the actual order, matches the PDF; Option 2 = stale
  6/17 proposal). PDF is a 1-page print of Option 1.

## Etere facts (verified live)

- ANAGRAF: agency **Ntooitive = 299** (15%), customer **LA Care Health Plan
  = 300**, AGENZIA link 299 → `_resolve_customer` pattern works.
- 8 prior contracts, code convention **`Ntooitive LACHP <YYMM>`**, desc
  `LA Care HP - <campaign> <startYYMM>-<endYYMM>` (e.g. 1879 = CRC 2506).
- Prior BNS lines: `BNS Mand ROS`, `BNS Korean ROS`, `BNS Chinese ROS` —
  booking 10, Rotation, windows matching shared `ROS_SCHEDULES`
  (Chinese M-Su 6a-11:59p, Korean M-Su 8a-10a).
- Not in customers.db → gather upserts the record.

## Plan

- [x] 1. Extract shared late-start planner from `crispin_automation.py` into
      `browser_automation/line_planner.py` (`_plan_ranges`, `_active_days`,
      `_WeekCol`, `_parse_date`, `_broadcast_yymm`, `_confirm_start_date`
      core, `_verify_production_charge`); Crispin imports from it (behavior
      unchanged, tests still pass).
- [x] 2. `browser_automation/parsers/ntooitive_parser.py`
      - `parse_ntooitive(path)` dispatcher (xlsx direct; pdf → sibling xlsx
        by stem, else raise with a clear message)
      - Column map by HEADER LABEL (DART rule); week dates from their own
        header cells; grid stops at Total Paid/Total Bonuses footers
      - Charges: `Gross Translation Fees` header row → NtooitiveCharge
      - Market map from `Market:` header ("Los Angeles"/"Spectrum" → LAX;
        beware template typo "Sepctrum")
      - Reconcile & RAISE: per line sum(weeks)==Total Unit#, rate×units==
        Line Gross; footer units both classes; footer gross == header
        `Gross (Airtime)`; header Gross Amount == airtime + translation;
        NET/gross ratio consistent across paid lines (implied commission)
- [x] 3. `browser_automation/ntooitive_automation.py` (Crispin clone)
      - gather: option-sheet pick (default = latest revised sheet), order
        summary, **late-start prompt + `_line_plan` preview** (per-range
        max/day, short-week split, entered-of-ordered guard), customer
        resolve via AGENZIA=299, customers.db upsert, code/desc defaults
        `Ntooitive LACHP <YYMM>` / `LA Care HP - CRC Campaign <YYMM>-<YYMM>`
      - entry: one contract, NYC master, `lookup_customer_defaults=True`
        (15% from ANAGRAF), paid lines verbatim gross rates, bonus via
        ROS_SCHEDULES, translation $ → Production box on first paid line +
        in-transaction CONTRATTISPESE verify, single transaction
- [x] 4. Registration sweep (checklist):
      enums (OrderType/KNOWN_AGENCY_KEYWORDS/SeparationInterval),
      detection (`_is_ntooitive` = "Crossings TV Media Proposal"+"Ntooitive"
      BEFORE Charmaine fallback; `extract_client_name`;
      `_detect_xlsx_content` NTOOITIVE branch; bump `_SCAN_CACHE_VERSION`),
      orchestrator `_INPUT_GATHERERS`, processing service dispatch +
      `_DIRECT_DB_ORDER_TYPES` + `_process_ntooitive_order` (parser import
      must match bridge — AST test), parser_bridge `_DISPLAY_NAMES` +
      `_REGISTRY` + `_DIRECT_DB_KEYS` + `_DIRECT_DB_TESTED_KEYS` +
      `_normalize_ntooitive`, `AGENCY_IDS["NTOOITIVE"] = 299`
- [x] 5. Tests: commit the xlsx as a fixture; positive parse (2 paid + 2
      bonus, 108/96 spots, $12,000 airtime, $800 translation, LAX, Option 1
      picked); tamper guards (blanked rate, dropped week cell, renamed
      column, wrong footer) must refuse; late-start planner cases; both
      Option sheets parseable on demand
- [x] 6. Verify end-to-end with the real file (gather dry-run), run suite

## Decisions (Lee, 2026-08-14)

1. **Dual-window daypart** ("M-F 6a-7a & 8p-9p") → ONE line with the union
   window 6a-9p (matches contract 1879's mixed-pattern precedent). Keep the
   IO's full daypart text in the description.
2. **PDF reader: build it** — `parse_ntooitive(path)` dispatches on
   extension (Crispin pattern); PDF read by word coordinates with
   header-label column mapping, same reconciliation guards.
3. **Option tabs** — prompt at gather, default = sheet with the latest
   revised/proposed date.

## Review (2026-08-14, complete)

Everything shipped and verified in one pass:

- `browser_automation/line_planner.py` — late-start planner extracted from
  Crispin (both automations share it; Crispin's 99 tests still pass through
  the aliases).
- `browser_automation/parsers/ntooitive_parser.py` — xlsx (label-mapped
  columns, option-sheet selection defaulting to the latest revised date) +
  coordinate-based PDF reader; both produce identical orders from the real
  REV 2 files, reconciling $12,000 airtime / $800 translation / 15% implied
  commission. The PDF's Language and Day Part columns overlap by ~1pt, so
  that split is by content (first day/time token), not geometry.
- `browser_automation/ntooitive_automation.py` — Crispin-pattern gather +
  single-transaction entry. Start date ALWAYS asked; the plan preview showed
  the 8/18 mid-week start correctly (Mandarin short week Tue–Fri → 2/day on
  its own line; 204/204 spots; a hypothetical 8/26 start drops 17 with
  notes). Customer auto-resolved to ANAGRAF 300 via agency 299; defaults
  reproduce the prior-contract naming (`Ntooitive LACHP 2608` /
  `LA Care HP - CRC Campaign 2608-2612`). Translation $800 → Production box
  on the first paid line, verified in-transaction.
- Registration complete in all seven layers; scan-cache bumped to v4; PDF,
  xlsx-content, and filename detection all return NTOOITIVE (the PDF was
  previously claimed by the Charmaine template fallback).
- 30 new tests in `tests/unit/test_ntooitive_parser.py` (fixtures = the real
  files): positive parse, PDF↔xlsx equivalence, tampered-workbook and
  tampered-word-stream refusals with no-op controls, planner cases.
  Full suite: 553 unit + 25 integration, all green.
- `CTV_Customers` (shared SQL table — the sqlite file is legacy) now carries
  LA Care Health Plan / 300 / LACHP / ntooitive defaults.

NOT done (deliberately): no Etere contract was entered — run the order
through the normal queue when ready.
