# Admerasia — Seoul Medical Group (SMG) client (2026-09-04)

Same IO layout as McDonald's; only the CLIENT differs. One order type, one parser, a
client table — do not fork a second OrderType.

- [x] `admerasia_parser.py`: `AdmerasiaClient` profile table (McD 42 (3,5,0) 'McD' / SMG 478
      (15,0,0) 'SMG'), resolved from the `Ref:` line ONLY (notes mention McDonald's on SMG IOs);
      `AdmerasiaOrder.client_name` / `.client`; code/description/notes builders read the profile
- [x] `admerasia_automation.py`: `lookup_customer` keyed on the profile (DB by client name →
      profile fallback); gather prints the real client; self-learning save via CustomerRepository
- [x] `order_detection_service.py`: `_is_admerasia` accepts Admerasia + `Ref:` + `Order Number:`
      (no McDonald's needed); bump `_SCAN_CACHE_VERSION` → 9
- [x] `parser_bridge._normalize_admerasia`: client from the order, not "McDonald's"
- [x] vision prompt text client-neutral; datamover CLIENT_AGENCY gets SMG
- [x] CTV_Customers row for SMG (478, ADMERASIA, SMG, 15/0/0, Seoul Medical Group)
- [x] tests: detection (SMG text w/o McDonald's; Admerasia-only still refused), client
      resolution with McDonald's in the notes, code/desc for both clients, bridge client
- [x] verify: parse both SMG IOs → `Admerasia SMG 1SE 2610` / `Seoul Medical Group Est 1 SEA 2610`
      and `2SE 2610`; McD fixture unchanged; pytest + ruff clean
- [x] memory note + commit + push + post_push.sh

## Review
- Both SMG IOs parse with the existing vision+positional reader (108 spots each, totals foot);
  codes `Admerasia SMG 1SE 2610` / `2SE 2610`, descriptions `Seoul Medical Group Est N SEA 2610`,
  customer 478, separation (15,0,0). Gather dry-run (piped answers) clean; SMG row self-learned
  into CTV_Customers and the second lookup comes from the table.
- 14 new tests (`tests/unit/test_admerasia_clients.py`); 724 unit tests pass; ruff clean.
- Found, not changed: McDonald's CTV_Customers row has `order_type='ADMERASIA'` (uppercase) →
  `OrderType()` rejects it → McD has always used the hardcoded (3,5,0), not the row's 15/0/0.
- `lookup_customer` now returns separation as (customer, ORDER, event) — the order
  `add_contract_line` expects; the old code returned (customer, event, order) from a DB hit.
