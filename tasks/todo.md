# Backwrite Pipeline — NEXT: multi-contract fix (NOT done)

Spec: tasks/backwrite-pipeline.md — see the "⚠ KNOWN GAP" bullet in §3.

Phases 0–4 work for SINGLE-contract orders. **Multi-contract PDFs are broken:**
a Toyota HL PDF made 3 contracts (2949/2950/2951, one manifest); the awaiting
flow backwrote only contract[0], then archived the whole manifest — the other
two were dropped. (Found 2026-07-13; Lee out of time that night.)

Three coupled causes (all in the awaiting flow, orders.py + app.js):
- [ ] `list_awaiting_backwrite` (~orders.py:1866) returns one row per manifest FILE,
      not per contract → expand to one row per (manifest, contract_index), labeled by code
- [ ] `doBackwrite` + contact modal (app.js) always POST with no contract_index (defaults
      to 0) → each row must carry + send its own contract_index (GET /contact already takes it)
- [ ] POST archives the whole manifest on first success → archive to Used/ ONLY when every
      contract is backwritten. Track completed indexes (e.g. write `backwritten:[...]` into
      the manifest); archive when the set is complete.

Verify: enter/refetch a 3-contract manifest → 3 awaiting rows → backwrite each →
manifest stays until the 3rd, then archives. Single-contract path must still work.

Then Phase 5 (legacy cutover) after a full clean billing cycle.
