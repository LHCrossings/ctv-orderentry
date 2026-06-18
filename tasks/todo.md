# Project TODO

## Completed
- [x] Fix market code mismatch — `etere_session.py` had 6 wrong Etere integer IDs
- [x] Consolidate market→Etere-ID mapping into `Market.etere_id` (single source of truth)
- [x] Fix stale `"C/M"` references in docs and tests (correct value is `"M/C"`)

---

## Critical

- [x] **Implement or delete `_move_processed_files()`** — deleted stub + 3 callers
- [x] **Delete `LegacyProcessorAdapter`** — deleted class + tests
- [x] **Delete `detect_order_type_legacy()` and `detect_order_type_from_pdf()`** — deleted both
- [x] **Delete `detect_customer()` deprecated wrapper** — deleted function + tests

---

## High Priority

### Project infrastructure

- [x] **Add `pyproject.toml`** — created with project metadata, pytest, ruff config; replaces `requirements-dev.txt`.

- [x] **Add pre-commit hooks** — `.pre-commit-config.yaml` with ruff lint + format.

- [x] **Set up GitHub Actions CI** — `.github/workflows/ci.yml` runs `ruff check` and `pytest -q` on push/PR.

### Silent exception handling

- [x] **Annotate bare `except Exception`** — added clarifying comments to all 3 intentional fallbacks:
  - `src/business_logic/services/pdf_order_detector.py:170` — heuristic table parse, non-fatal
  - `src/presentation/cli/input_collectors.py:419`, `:479` — best-effort DB lookups

### Type hints

- [x] **Replace `any` (lowercase) with `Any`** — fixed all instances in
  `src/business_logic/services/order_processing_service.py`.

### Unused imports

- [x] `src/business_logic/services/order_processing_service.py:23–24` — removed `Customer` and `OrderStatus`.
- [x] `src/orchestration/orchestrator.py:15` — removed `import shutil`.

---

## Medium Priority

### Function length (>100 lines — violates CLAUDE.md limit)

- [x] Extracted `_run_{tcaa,misfit,sagent,charmaine}_with_driver()` helpers; all 4 main functions
  now ≤65 lines, all helpers ≤25 lines.

### Repeated dispatch pattern (DRY violation)

- [x] **Refactor agency input-gathering dispatch** — replaced 6 identical blocks in
  `orchestrator._process_orders_interactive()` with `_INPUT_GATHERERS` registry dict +
  single generic handler (importlib dynamic import).

- [x] **Dispatch in `_process_single_order()`** — replaced 10-way if/elif with
  `_PROCESSOR_DISPATCH` class dict + `getattr`.

### Incomplete implementation

- [x] **`_process_daviselen_order()` TODO comment** — replaced vague TODO with clear
  explanation: Daviselen automation is one-directional (Python → browser); contract numbers
  must be retrieved from Etere manually.

---

## Test Coverage Gaps

- [x] **Fixed 3 collection errors** (`test_orchestrator.py`, `test_order_scanner.py`,
  `test_config.py`) by creating `tests/conftest.py` with global pdfplumber/selenium mocks.
  Also fixed `test_order_scanner.py` mocks to match actual `detect_multi_order_pdf` API.
- [x] **Added dispatch coverage** to `test_order_processing_service.py`:
  `TestProcessorDispatch` (6 tests) and `TestOrderGroupingLogic` (4 tests) covering
  `_PROCESSOR_DISPATCH`, `_process_single_order` routing, `_create_stub_result`, and
  TCAA-by-PDF batch grouping.
- [x] All other modules (`orchestrator.py`, `order_scanner.py`, `customer_repository`,
  `input_collectors.py`, `output_formatters.py`) have existing test files that now run.

**Result: 250 tests passing, 0 collection errors.**

---

## RPM Conversion

- [x] **Create `browser_automation/rpm_automation.py`** — full automation following Daviselen
  pattern: `gather_rpm_inputs()` + `process_rpm_order()`, DB-based customer lookup,
  weekly spot distribution loop, language-specific lines, bonus line handling,
  separation (25, 0, 15), universal agency billing. Blocks tab intentionally skipped
  (matches all new automations per Admerasia pattern).
- [x] **Add RPM to `_PROCESSOR_DISPATCH`** in `order_processing_service.py` (now 11 types).
- [x] **Add `_process_rpm_order()`** to `order_processing_service.py` — mirrors
  `_process_daviselen_order` exactly.
- [x] **Add RPM to `needs_browser` list** in `order_processing_service.py`.
- [x] **Add RPM to `_INPUT_GATHERERS`** in `orchestrator.py`.
- [x] **Update dispatch test** — `test_dispatch_dict_covers_all_automated_types` now
  expects 11 types; fallback test changed from RPM → WORLDLINK.

**Result: 250 tests passing, 0 failures.**

---

## LRCCD / 3Fold Communications parser (NEW — in progress)

Source: `3FOLD_LRCCD Fall&Spring Enrollment 26-27_AIRTIME_Signed.pdf`

### Facts (verified against DB)
- Text-based PDF, 2 pages = **2 orders**: FALL 2026 (7/20/26–8/23/26), SPRING 2027 (11/10/26–1/17/27).
- Each order has **AIRTIME :30** and **AIRTIME :15** sections → per-line duration 30/15.
- Single market **CVC**. Advertiser **Los Rios Community College** = customer **218**, linked agency **203 = 3Fold Communications**, media center 316.
- Agency / gross rates / 15% commission → `rates_are_net=False`; commission auto via `create_contract_header(lookup_customer_defaults=True)`. ($2,499.20 × 0.85 = $2,124.32 ✓)
- **No weekly columns** → `spots_per_week=0` → EtereDirectClient auto **Rotation**. Bonus → Rotation.
- ROS bonus (Chinese/Filipino/Vietnamese) → `ROS_SCHEDULES[lang]`; Hmong bonus has its own daypart.

### Create
- [ ] `browser_automation/parsers/lrccd_parser.py` — `parse_lrccd_pdf` → `LRCCDDocument`
- [ ] `browser_automation/lrccd_automation.py` — `gather_lrccd_inputs` + `run_lrccd_order` (one contract per season)

### Edit (registration, all at once — lesson #7)
- [ ] `etere_direct_client.py` AGENCY_IDS["3FOLD"]=203
- [ ] `enums.py` LRCCD
- [ ] `orchestrator.py` _INPUT_GATHERERS
- [ ] `order_processing_service.py` dispatch + _DIRECT_DB_ORDER_TYPES + _process_lrccd_order
- [ ] `parser_bridge.py` _DISPLAY_NAMES + _REGISTRY + _DIRECT_DB_KEYS + _DIRECT_DB_TESTED_KEYS
- [ ] `order_detection_service.py` _is_lrccd + wire into detect_from_text

---

## AI Fallback Parser (Claude extraction) — IN PROGRESS (2026-06-18)

Use Claude to extract orders that have no deterministic parser (or whose parser is broken), feeding the existing entry engine. Opt-in, reviewed, parsers kept as the net.

### Done
- [x] `ai_parser.py` — `parse_ai_pdf` (messages.parse + schema) + `parse_ai_order` (cached `<file>.ai.json` sidecar so preview==entry, model runs once). Vision reads image PDFs.
- [x] `ai_fallback_automation.py` — gather + `run_ai_order`; ROS via ROS_SCHEDULES (+`_LANG_ALIASES` Mandarin→Chinese), weekly grids via `consolidate_weeks`, `lookup_customer_defaults=True`.
- [x] `scripts/ai_parser_eval.py` — read-only extract + diff vs trusted parser.
- [x] Full registration (enum, orchestrator, service dispatch+`_DIRECT_DB`+`_process_ai_fallback_order`, bridge ×3; NOT in `_DIRECT_DB_TESTED_KEYS`).
- [x] Triggers (opt-in, scanner = single seam, web preview inherits): `CTV_AI_FALLBACK` (UNKNOWN→AI), `CTV_CHARMAINE_AI` (single-contract Charmaine→AI; multi-contract stays on Charmaine).
- [x] `anthropic`+`pydantic` added to deps; `move_to_used` returns JSON on failure (+ deletes `.ai.json` sidecar).
- [x] Verified: LRCCD exact; image-based RPM correct; Solsken (Charmaine) exact where Charmaine mangled it. Breadth eval on Used/: rpm/timeadvertising/hl exact; igraphix/admerasia/sagent rate divergences (review needed).

### Future (requested)
- [ ] **Trade order entry in the AI parser** — AI recognizes trade orders but doesn't enter them as trade. Add `is_trade`/value to schema; `run_ai_order` pass `is_trade=True` (revenue_type=Trade); gather handles total-value-not-per-spot-rate. See "Trade entry direct DB write" lesson.
- [ ] Tighten rate extraction (gross vs net, per-spot column) — the igraphix/admerasia/sagent gaps — before expanding AI beyond Charmaine.
- [ ] After a few real reviewed Charmaine orders confirm: delete `charmaine_parser.py`, make AI the default for that format.
- [ ] Optional: browser-side line-by-line edit-before-enter (needs new preview UI + generic entry endpoint).

---

## LRCCD review — DONE (2026-06-18)
### Review — DONE (2026-06-18)
- Parser `parse_lrccd_pdf` + automation `gather_lrccd_inputs`/`run_lrccd_order` created; 8 registration points wired (AGENCY_IDS 3FOLD=203, enum, orchestrator, service dispatch + _DIRECT_DB + _process_lrccd_order, bridge ×4, detection _is_lrccd).
- Verified against the real PDF: 2 contracts (Fall 7/20–8/23/26, Spring 11/10/26–1/17/27); 44 lines; spots_per_week=0 → Rotation; booking 28 paid / 16 bonus; 188 spots / 152 paid; $2,499.20 gross/flight; commission via ANAGRAF (lookup_customer_defaults=True, customer 218 → agency 203). Times incl. 12a→23:59, 6-8p→18:00-20:00, spaced "M - F ( 4 p -7p)"→M-F 16:00-19:00 all correct.
- `pytest`: 256 passed. `ruff`: clean. Dry-run done with DB monkeypatched — **not yet written to live Etere**.
