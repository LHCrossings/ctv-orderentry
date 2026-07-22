# Crispin / Bay Area AQMD Parser

New direct-DB parser. Source: `Crossings TV Media Proposal_BAAQMD_2026_REV1.xlsm` (+PDF).

## Facts (confirmed)
- Agency: **Crispin LLC** → ANAGRAF agency **446** (`AGENCY_IDS["CRISPIN"]`)
- Advertiser: **Bay Area AQMD** → customer **448** (BAAQMD has TWO Etere entries: 183=Allison&Partners/AGENZIA 187, 448=Crispin/AGENZIA 446). Disambiguate by `ANAGRAF.AGENZIA == agency_id`.
- **No agency commission** on either BAAQMD order.
- Market: **SFO** (San Francisco Bay Area — Xfinity 3131 / KQTA 15.3). Single market.
- Rate = **Discounted Rate** column (F), NOT Unit Value (E). Rate 0 ⇒ **bonus**.
- Paid = 4 × :30s News lines (explicit dayparts). Bonus = 4 × :15s ROS lines.
- Bonus ROS windows (Lee-confirmed): Cantonese M-F 7p-11:59p · Mandarin M-Su 8p-11:59p · Filipino M-Su 4p-7p · Vietnamese M-Su 10a-1p.
- Per-line duration from Length col (:30s / :15s).
- 14 weekly columns 7/27→10/26 (last week fewer spots → consolidate splits). Broadcast Aug–Nov 2026 = 2608–2611.
- Contract code default: `Crispin BAAQMD 2608`; description: `Bay Area AQMD 2608-2611` (Lee-given).
- Production/translation costs: **disregard for now** (Lee).
- `rates_are_net=False` (no commission → no gross-up).
- Trap: PDF signature line names "Allison + Partners" — a template leftover. Use the header **Agency** field only.

## Build
- [ ] `browser_automation/parsers/crispin_parser.py`
- [ ] `browser_automation/crispin_automation.py`
- [ ] `src/domain/enums.py` — `OrderType.CRISPIN`
- [ ] `browser_automation/etere_direct_client.py` — `AGENCY_IDS["CRISPIN"] = 446`
- [ ] `src/orchestration/orchestrator.py` — `_INPUT_GATHERERS`
- [ ] `src/business_logic/services/order_processing_service.py` — handler + `_DIRECT_DB_ORDER_TYPES` + `_process_crispin_order`
- [ ] `src/web/parser_bridge.py` — dispatch + display name + `_DIRECT_DB_KEYS` + `_DIRECT_DB_TESTED_KEYS`
- [ ] `src/orchestration/order_scanner.py` — `_detect_xlsx_content` "CRISPIN"
- [ ] `src/business_logic/services/order_detection_service.py` — `detect_from_filename` "CRISPIN"

## Verify — DONE
- [x] parse → 8 lines; reconciled paid=162 / bonus=164 vs footer
- [x] day strings valid (paid split; bonus via ROS map); parse_day_bits ≥1 flag each
- [x] dry-run entry (rolled back): 1 header + **16 lines** (8×2 ranges), blocks auto-assigned, customer 448, agency 446
- [x] commission: fixed shared `create_contract_header` 0%-clobber → Crispin P_AGENZIA=0.00; T&T (agency 439) still 15.00
- [x] ruff clean, all files compile, 7 registration points wired + detection

## Review
- All 8 build items + verify complete. Fully dry-run-verified against live DB (rolled back).
- Shared fix: `agency_pct` now honors the ANAGRAF-linked commission (no 15% override of a real 0%). Blast radius nil — every other agency has Commissione=15.
- **Pending:** Lee runs the first real entry to commit; production costs deferred.
