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

- [ ] `_process_sagent_order()` — 131 lines
  `src/business_logic/services/order_processing_service.py:871`
- [ ] `_process_charmaine_order()` — 127 lines
  `src/business_logic/services/order_processing_service.py:1003`
- [ ] `_process_misfit_order()` — 126 lines
  `src/business_logic/services/order_processing_service.py:654`
- [ ] `_process_tcaa_order()` — 119 lines
  `src/business_logic/services/order_processing_service.py:534`

### Repeated dispatch pattern (DRY violation)

- [ ] **Refactor agency input-gathering dispatch** —
  `src/orchestration/orchestrator.py:231–395` repeats the same
  `if order_type == X: gather_X_inputs()` pattern 8+ times. Replace with a registry dict
  mapping `OrderType → callable`.

- [ ] **Same pattern in `_process_orders_interactive()`** —
  `src/business_logic/services/order_processing_service.py:220–435`.

### Incomplete implementation

- [ ] **`_process_daviselen_order()` returns empty contracts list**
  `src/business_logic/services/order_processing_service.py:843` — TODO comment says
  "Extract contract numbers from automation". Either implement or make the limitation explicit.

---

## Test Coverage Gaps

The following modules have zero tests:

- [ ] `src/business_logic/services/order_processing_service.py` (1,824 lines)
- [ ] `src/orchestration/orchestrator.py` (592 lines)
- [ ] `src/orchestration/order_scanner.py` (160 lines)
- [ ] Customer repository operations
- [ ] `src/presentation/cli/input_collectors.py` and output formatters
