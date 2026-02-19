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
