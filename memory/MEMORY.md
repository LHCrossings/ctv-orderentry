# ctv-orderentry Project Memory

## Project Overview
CTV order-entry automation: Python → Etere (broadcast traffic system) via Selenium.
All automations are one-directional (Python drives browser; no return data from Etere).

## Key Patterns

### Agency Automation Pattern
Each agency follows: `gather_{agency}_inputs(pdf_path)` + `process_{agency}_order(driver, pdf_path, user_input=None)`.
Inputs gathered upfront (before browser session opens) via `_INPUT_GATHERERS` in orchestrator.
Template: see `browser_automation/daviselen_automation.py`.

### Dispatch Registries
- `_PROCESSOR_DISPATCH` in `order_processing_service.py` — maps OrderType → method name (12 agencies as of WorldLink addition)
- `_INPUT_GATHERERS` in `orchestrator.py` — maps OrderType → (module, fn_name, display_name) for upfront input gathering
- Both use `importlib.import_module` / `getattr` to avoid giant if/elif chains

### Function Length Limits (CLAUDE.md)
- Main functions: ≤65 lines
- Helper functions: ≤25 lines
- Long functions get `_run_{agency}_with_driver()` helpers extracted

### Blocks Tab
Intentionally skipped in all new automations (EtereClient: General → Options → Save).
Old code's `_filter_blocks_by_prefix` is legacy — do NOT add blocks tab to new automations.

### EtereClient Utilities
- `EtereClient.parse_time_range("6a-8p")` → `("06:00", "20:00")` — handles abbreviated formats
- `EtereClient.check_sunday_6_7a_rule(days, time_range)` → `(adjusted_days, day_count)`
- `add_contract_line(contract_number, market, start_date, end_date, days, time_from, time_to, description, spot_code, duration_seconds, total_spots, spots_per_week, rate, separation_intervals)`

### RPM Specifics
- Parser: `parse_rpm_pdf(pdf_path) -> (RPMOrder, list[RPMLine])` — lines returned separately
- `RPMLine.is_bonus` is a **bool field** (not a method)
- `RPMLine.duration` is `"00:00:30:00"` format → convert with `_duration_to_seconds()`
- `RPMLine.daypart` is `"M-F 6a-8p Chinese"` → split(" ", 2) for (days, time, language)
- Market already in code form (SEA/SFO/CVC) — no mapping needed
- Separation: `SeparationInterval.RPM.value` = `(25, 0, 15)`
- Billing: `BillingType.CUSTOMER_SHARE_AGENCY` (universal agency)

### WorldLink Specifics
- Parser: `parse_worldlink_pdf(pdf_path)` → dict with `lines[]`, `network` (CROSSINGS/ASIAN), `order_type` (new/revision_add/revision_change), `order_code`, `description`, `tracking_number`, `advertiser`
- Line fields: `line_number`, `action`, `start_date`, `end_date`, `from_time` (24-hr HH:MM), `to_time`, `time_range` (12-hr string), `duration` (seconds as str), `spots` (spots/wk), `total_spots`, `rate` (str), `days_of_week`
- Crossings TV: NYC line (real rate) + CMP line ($0) per PDF line — CMP replicates via block refresh
- Asian Channel: DAL market only — single line per PDF line
- `process_worldlink_order()` returns `Optional[str]` (contract_number), not bool — needed for Contract entity + block refresh tracking
- Revision orders: prompt for existing contract_number; `highest_line = lines[0]['line_number'] - 1`
- `requires_block_refresh()` = True for WORLDLINK only — user must manually refresh in Etere after CMP lines added

### Testing
- `tests/conftest.py` mocks pdfplumber + selenium globally (prevents collection errors)
- `test_dispatch_dict_covers_all_automated_types` — update when adding new agencies (currently 12)
- Fallback dispatch test uses `OrderType.UNKNOWN` (not in `_PROCESSOR_DISPATCH`)

## Key File Paths
- `src/business_logic/services/order_processing_service.py` — dispatch, processing methods
- `src/orchestration/orchestrator.py` — `_INPUT_GATHERERS`, interactive processing
- `browser_automation/etere_client.py` — `EtereClient` (contract header + lines)
- `browser_automation/parsers/rpm_parser.py` — RPM PDF parser
- `src/domain/enums.py` — `OrderType`, `SeparationInterval`, `BillingType`, `Market`
- `tests/conftest.py` — global test mocks

## Universal Booking Rules

### Split Time Ranges (e.g. "4p-5p; 6p-7p")
Book as **one Etere line using the max range** (4p-7p). The operator manually removes the
intervening programming in Etere afterward. This commonly occurs on Filipino dayparts.
Never split into two Etere lines for a semicolon time range — always use max range.
Source: `archived scripts/CHARMAINE_GENERIC_TEMPLATE_DOCUMENTATION.md` line 424.

### Week Consolidation (universal — applies to every order/agency)
Group consecutive weeks with the **same spot count** into a single Etere line.
Split into a new line whenever: (a) the spot count changes, or (b) there is a gap (0-spot week).
Example: weekly spots [3,3,3,4,4] → 2 Etere lines (3 wks @ 3/wk, then 2 wks @ 4/wk).
Non-contiguous flight windows (e.g. May block + Aug/Sep block) are always separate lines.
Source: `consolidate_weeks()` in archived scripts; documented in TCAA/SAGENT/Misfit docs.

### Bonus Lines ($0 rate with spots ordered)
Any line with $0.00 rate AND spots > 0 on a given week is **always entered** in Etere as
**BNS** (bonus billing type). Lines with 0 spots on every week are skipped.

## Separation Intervals by Agency
All defined in `SeparationInterval` enum in `src/domain/enums.py`:
- RPM: (25, 0, 15)  WORLDLINK: (5, 0, 15)  OPAD: (15, 0, 15)
- HL: (25, 0, 0)  DAVISELEN default: (15, 0, 0)  ADMERASIA: (3, 0, 5)

## DB Access Pattern
`db = container.get("database_connection")` then `with db.connection() as conn:`
All revenue queries exclude Trade: `WHERE (revenue_type != 'Trade' OR revenue_type IS NULL)`
