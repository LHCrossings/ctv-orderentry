# San Joaquin County parser (client 451, CVC, PO → Customer Order ref)

Source: `San Joaquin County Voter Registration General 2026.xlsm` (one sheet
"SJ County"). Charmaine-family proposal grid, but its own column layout:
`Insertion | Time | Value | 8 week-date columns | Units | TOTAL`, a paid block
ending at "Total Paid", a "BONUS (30seconds)" block ending at "Total Bonuses",
a "Production Services" block ("Voiceover translation fees: $2,650"), and a
"Summary of Contract" footer (Total Airtime 6010 / Voiceover 2650 / Total 8660).
Flight 9/14/2026–11/3/2026: 7 full weeks + a Mon–Tue stub week (11/2–11/3).

Facts from Lee: customer = ANAGRAF **451 "San Joaquin County"** (direct, no
agency, 0% — verified live), market **CVC**, production $2,650 → Production box
on the FIRST paid line, PO number asked at gather → `customer_order_ref`.

## Plan
- [x] `browser_automation/parsers/sjcounty_parser.py` — `SJCountyLine/Charge/Order`,
      `parse_sjcounty(path)`; columns mapped by header LABEL (`Insertion`,
      `Time`, `Value`, `Units`, `TOTAL`; week cols = datetime header cells);
      paid rows until `Total Paid`, bonus rows until `Total Bonuses`;
      "Voiceover translation fees: $N" → charge; `_money()` → None never 0.
      Reconcile + RAISE: per line sum(weeks)==Units and Units×Value==TOTAL;
      Total Paid / Total Bonuses rows (units + dollars); Summary Total Airtime
      == paid gross, Voiceover == charge, Total == both. `rates_are_net=False`
      (0% customer → gross==net anyway).
- [x] Dayparts: paid "M-Sun 7p-9p/ M-F 11:30p-12a" → ONE line, union days M-Su,
      time 7p-12a (Ntooitive dual-window convention); "M-F 4p-5p, 6p-7p" → M-F
      4p-7p; bonus rows are ROS → `ROS_SCHEDULES[language]` (Chinese/Filipino/
      Vietnamese/Hmong/Punjabi→South Asian). Sunday 6-7a rule applied.
- [x] `browser_automation/sjcounty_automation.py` — `gather_sjcounty_inputs`
      (summary preview, **PO number prompt (required)**, market [CVC], start date
      via `confirm_start_date(always_ask=True)` + `plan_ranges` preview, customer
      ID [451], contract code/description bracket defaults, separation from
      customers.db else (15,0,0), billing_type from db else 'direct', upsert
      customers.db) and `run_sjcounty_order` → `[(code, ok)]`. Header:
      `customer_id`, `agency_id=None`, `lookup_customer_defaults=True`,
      `customer_order_ref=PO`, `billing_type`, `allow_rename=True`. Lines via
      shared `_line_plan` (consolidate_weeks + plan_ranges; stub week 11/2–11/3
      gets its own cap), `booking_code=10 if bonus else 2`, `duration="30"`,
      `production_cost` on first paid line + `verify_production_charge` in-txn.
- [x] Registration (all at once): `OrderType.SJCOUNTY="sjcounty"`;
      orchestrator `_INPUT_GATHERERS`; parser_bridge display name, `_REGISTRY`,
      `_DIRECT_DB_KEYS`, `_DIRECT_DB_TESTED_KEYS`; service `_PROCESSOR_METHODS`,
      `_DIRECT_DB_ORDER_TYPES`, `_process_sjcounty_order`; detection: xlsx
      content peek (`San Joaquin County` cell — client is the definer, per the
      Wallrich lesson) + filename rule, tested BEFORE the Charmaine fallback;
      bump `_SCAN_CACHE_VERSION`.
- [x] Tests `tests/unit/test_sjcounty_parser.py` — commit the xlsm as fixture;
      totals (141 paid/113 bonus, $6,010/$2,650/$8,660), line shapes, stub-week
      handling, tampering negatives (blank rate, dropped week cell, wrong Units,
      wrong footer, unclassified production text, no-op mutation still parses),
      `_line_plan` late-start; structural test that handler import == bridge.
- [x] `uv run pytest tests/unit`, `ruff check/format`, dry-run the gather
      against the real file; commit + push.

## Decisions (Lee, 2026-08-27)
1. Chinese paid line: ONE line, M-Su 7p-12a (union).
2. Code `SJ County 2609`; description `San Joaquin County Voter Registration - General Election 2026`.

## Review
- Parser reconciles every printed total (per line, footers, summary); 12 tamper
  negatives via the `_load_rows` seam (formula cells lose cached values on an
  openpyxl re-save, so tampering the row grid is the only faithful layer).
- Planner: 7 full weeks + 11/2–11/3 stub line per airtime row (Hmong's zero stub
  dropped), 254/254 spots; Wed late start keeps caps valid without a split.
- Bridge `_line_language` now trusts the line's `language` field before scanning
  the description — the Chinese block was being validated as Cantonese.
- Gather dry run OK; PO stored verbatim in `customer_ref` → CUSTOMERREF.
- Not yet exercised: the live Etere write (first real entry = Lee's).
