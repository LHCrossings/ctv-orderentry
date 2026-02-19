# Project TODO

## Completed
- [x] Fix market code mismatch — `etere_session.py` had 6 wrong Etere integer IDs
- [x] Consolidate market→Etere-ID mapping into `Market.etere_id` (single source of truth)
- [x] Fix stale `"C/M"` references in docs and tests (correct value is `"M/C"`)

---

## Critical

- [ ] **Implement or delete `_move_processed_files()`**
  `src/orchestration/orchestrator.py:551` — method body is a loop of `pass`. Documented to
  move processed files to archive directories but does nothing. Implement it or delete and
  remove all callers.

- [ ] **Delete `LegacyProcessorAdapter`**
  `src/business_logic/services/order_processing_service.py:1644–1752` — never instantiated
  or used anywhere. Dead code per "Replace, don't deprecate" rule.

- [ ] **Delete `detect_order_type_legacy()` and `detect_order_type_from_pdf()`**
  `src/business_logic/services/pdf_order_detector.py:403–422` — backward-compat wrappers.
  Verify no callers, then remove.

- [ ] **Delete `detect_customer()` deprecated wrapper**
  `src/business_logic/services/customer_matching_service.py:211–243` — verify no callers,
  then remove.

---

## High Priority

### Project infrastructure

- [ ] **Add `pyproject.toml`** — no packaging or tool config exists. At minimum: project
  metadata, `[tool.pytest.ini_options]`, `[tool.ruff]`, `[tool.ty]`.

- [ ] **Add `prek` hooks** — `prek install` + configure ruff, ty, and pytest as pre-commit
  checks per CLAUDE.md.

- [ ] **Set up GitHub Actions CI** — run `pytest -q` and `ruff check` on push/PR.

### Silent exception handling

- [ ] **Fix bare `except Exception: pass`**
  - `src/business_logic/services/pdf_order_detector.py:170` — swallows errors silently
  - `src/presentation/cli/input_collectors.py:167` — no error logging
  - `src/presentation/cli/input_collectors.py:419`, `:479` — catch too broadly

### Type hints

- [ ] **Replace `any` (lowercase) with `Any`** — 17 instances in
  `src/business_logic/services/order_processing_service.py` (lines 38, 105, 170, 223, 313,
  401, 534, 654, 784, 874, 1006, 1134, 1226, 1312, 1403, 1494, 1664). Breaks static type
  checking.

### Unused imports

- [ ] `src/business_logic/services/order_processing_service.py:23–24` — `Customer`,
  `OrderStatus` imported but unused.
- [ ] `src/orchestration/orchestrator.py:15` — `shutil` imported but unused.

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
