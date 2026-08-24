# Multi-estimate manifests: backwrite every contract, not just contracts[0]

Bug (Sac County Voters; will recur on HL ACM Q4 Toyota 3026-3028): a manifest
with N contracts backwrites only contracts[0] (UI never sends contract_index),
then archives the WHOLE manifest to Used/ on the first green reconcile — the
other contracts silently never backwrite. Reconciliation also silently no-ops
for these manifests (io_detail has sub_orders, top-level lines=[]), and the
top-level rates_are_net roll-up is missing for multi-order PDFs.

## Plan
- [x] parser_bridge.py: multi-order roll-up carries rates_are_net (any sub_order)
- [x] orders.py: module helper `_io_detail_for_contract(manifest, idx)` —
      unique estimate-in-code match first (old ' Est N' codes contain TWO
      numbers → ambiguity falls through), index match when counts equal
- [x] orders.py generate: per-contract io_detail for generate_excel /
      reconcile / gross-up; per-contract rates_are_net + estimate default;
      per-contract xlsx name; stamp contracts[idx].backwritten_at on green
      reconcile and archive ONLY when every contract is stamped
- [x] orders.py /contact: per-contract estimate default (sub_order, then
      CUSTOMERREF)
- [x] app.js: one Backwrite button per contract (data-contract-index),
      ✓ marks on backwritten contracts, index passed to /contact + generate
- [x] index.html: bump app.js ?v=
- [x] tests: _io_detail_for_contract matching + roll-up rates_are_net
- [x] verify: full suite

## Review
- Fix shipped in commit da2229e; tests in tests/unit/test_backwrite_multi_contract.py
  (8 tests: matching by unique estimate-in-code incl. the ambiguous old
  ' Est N' codes, index fallback, single-contract passthrough, count-mismatch
  None, rates_are_net roll-up). Full suite 615 passed.
- Recovery for already-archived multi-contract manifests (Sac County Voters):
  restore from Used/ back to Entered/ — the row reappears with per-contract
  buttons; already-backwritten contracts just get regenerated or skipped.
