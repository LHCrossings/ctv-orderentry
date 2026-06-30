# Task: Emerald Queen Casino (EQC) / TH Media parser

## Context / decisions (confirmed with user)
- Client: Emerald Queen Casino = ANAGRAF customer **20**. Agency: TH Media = **19**.
- Default contract code: `TH EQC <yymm>` (yymm = earliest week's year+month, e.g. `TH EQC 2607`).
- Default description: `Emerald Queen Casino <yy>Q<n>` (e.g. `Emerald Queen Casino 26Q3`).
- **One contract per quarter** ("separate by quarter"); all months of a quarter go in one contract.
- **Each date column = ONE week (Mon–Sun); weeks are non-consecutive (every other week).**
  → **One contract line per program per week-column** with spots>0. **No week consolidation.**
- Rates are **GROSS** (sum of rate×spots == the $3,000/mo GROSS row) → `rates_are_net=False`, no gross-up.
- Bonus = the three $0 language rows → booking_code 10, Rotation (uses their own time windows, not ROS).
- Vietnamese Drama `10a-11a & 12p-1p` → **one line 10a–1p** (user choice).
- Single market: Seattle → SEA. Spot duration default :30.

## Workbook layout (TH Media_ EQC_JulyAugSept.xlsx)
- C3 Crossings TV, C4 Seattle, C5 channel. Header row 8: B=PROGRAM, D=SCHEDULE, E=RATE, F..K = week dates.
- Rows 9–14 paid (rate>0); rows 15–17 bonus (rate 0). Stop at "Paid Units"/"Bonus Units"/"Total"/"GROSS".

## Plan
### New files
- [ ] `browser_automation/parsers/eqc_parser.py` — `EQCLine`, `EQCOrder`, `parse_eqc_xlsx()`; schedule→days/time split.
- [ ] `browser_automation/eqc_automation.py` — `gather_eqc_inputs()`, `run_eqc_order()`, `_create_eqc_contract()` (per quarter, per-week lines).

### Registration edits
- [ ] `src/domain/enums.py` — `OrderType.EQC = "eqc"`; separation default + `for_order_type` entry.
- [ ] `browser_automation/etere_direct_client.py` — `AGENCY_IDS["THMEDIA"] = 19`.
- [ ] `src/business_logic/services/order_processing_service.py` — dispatch + `_DIRECT_DB_ORDER_TYPES` + `_process_eqc_order`.
- [ ] `src/orchestration/orchestrator.py` — `_INPUT_GATHERERS[EQC]`.
- [ ] `src/web/parser_bridge.py` — `_DISPLAY_NAMES`, `_REGISTRY`, `_DIRECT_DB_KEYS`, `_DIRECT_DB_TESTED_KEYS`.
- [ ] `src/business_logic/services/order_detection_service.py` — filename detect (`EQC`/`EMERALD`/`TH MEDIA`).

### Verify
- [ ] Parse the real file: 6 paid + 3 bonus lines, 6 week-columns each; per-program week lines reconcile to 32 paid / 24 bonus per week.
- [ ] Dry-run line builder (no DB write) prints per-week lines with correct Mon–Sun ranges, days, times, booking codes.
- [ ] Confirm registration imports cleanly (parser_bridge lists EQC as direct-db + tested).

## Review
Built `eqc_parser.py` + `eqc_automation.py` and registered EQC across all 6 points
(enums, AGENCY_IDS, order_processing_service dispatch+direct-db+`_process_eqc_order`,
orchestrator `_INPUT_GATHERERS`, parser_bridge ×4, filename detection).

Verified against the real workbook (no DB writes):
- Detection: filename → `OrderType.EQC`. Bridge lists EQC as direct_db + tested.
- Parse: 6 paid + 3 bonus rows, 6 week-columns; days/times/rates/bonus all correct.
- One quarter contract `TH EQC 2607` / `Emerald Queen Casino 26Q3` (Jul 6 – Sep 27).
- **54 lines = 9 programs × 6 weeks**, each a single Mon–Sun week (no consolidation).
- Per-week reconciliation: paid=192, bonus=144 (32/24 per week ×6). Booking 2/10 correct.
- Vietnamese Drama entered as one line 10:00–13:00 (user choice). Midnight `7p-12a`→23:59.
- All edited + new modules import cleanly.

Not yet done: live DB entry (user is deleting the existing EQC orders first). The
automation is single-transaction per quarter with rollback on error.
