# Backwrite Pipeline — Phase 4 (contact prefill) ✓ DONE 2026-07-13

Spec: tasks/backwrite-pipeline.md §2.6 + Phase 4 line.
(Phase 3 reconciliation banner committed earlier today, 9e1bd29.)

Re-scoped mid-build (Lee): source the contact block LIVE from Etere ANAGRAF
and let the user override at review time — NOT customers.db, which drifts
across the Jumpbox/desktop machines.

- [x] `_contact_from_anagraf()` — poll bill-to (agency else committente) contact block
- [x] `GET /awaiting-backwrite/{file}/contact` — prefill source for the modal
- [x] POST endpoint: poll ANAGRAF base + overlay `{contact:{...}}` user overrides → user_inputs
- [x] Review modal (index.html `#bw-contact-overlay`, app.js `openBwContact`) — prefilled + editable
- [x] Backwrite button routes through the modal; Generate feeds the Phase 3 reconcile gate
- [x] Verify: live poll (contract 2887), TestClient GET (200/404), POST override lands in Excel

## Review

ANAGRAF fill for 2026 bill-to entities: address/city/zip ~80%, email 78%,
phone 65%, state 63%, contact-person 0%. The modal prefills what Etere has;
the user types contact-person + any gaps. Nothing persisted — ANAGRAF stays
the single source of truth.

Remaining pipeline: Phase 5 (legacy cutover) — after a full clean billing
cycle of parallel use, mark the /backwrite card "(legacy)" then delete.
Still-open watch item: first real H&L net-rate one-click (Phase 3 guards it).
