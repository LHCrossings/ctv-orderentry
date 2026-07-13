# Backwrite Pipeline — Phase 3 (reconciliation banner)

Spec: tasks/backwrite-pipeline.md §2.4. Resuming 2026-07-13.
(Prior tracker — EDI Billing Redesign, phases 0–3 done — lives in
tasks/edi-billing-redesign.md.)

The existing `validation_out` proves the Excel is internally self-consistent
(run sheet == SC lines == monthly). It CANNOT catch a wrong input that produces
a consistent-but-wrong Excel — which is exactly the July 2026 error class.
Phase 3 adds the manifest-IO-vs-Etere comparison.

- [x] 1. `reconcile_io_vs_etere()` in `src/backwrite/transformer.py`
      - gross-up direction error: Etere gross vs IO expected gross ratio ≈ 1/(1-fee) (double) or (1-fee) (missing)
      - paid-spot-count gap (revision / partial entry)
      - missing market ordered on the IO
      - returns {ok, messages, detail}; ok=True + no messages when nothing reliable to compare
- [x] 2. Wire into one-click endpoint (`awaiting_backwrite_generate`, orders.py)
      - merge into the `reconcile` dict (extend messages, AND ok, add io_check)
      - gate `_archive_entered` on reconcile.ok — a discrepancy stays in the queue
      - `X-Backwrite-Archived` header so the frontend knows
- [x] 3. Frontend `doBackwrite` (app.js): green auto-saves; red holds blob behind a confirm naming the discrepancy
- [x] 4. Merge into legacy `/generate` (backwrite.py) server-side — richer messages in the existing banner
- [x] 5. Verify: real contract → green; seeded rate mismatch → red names the ratio

## Review (2026-07-13)

Phase 3 done and verified. The reconciliation is a standalone
`reconcile_io_vs_etere()` (pure comparison of manifest-IO vs placement-CSV,
no Excel coupling), merged into the same `reconcile` payload both flows
already emit via `X-Backwrite-Reconcile`.

Behavior change worth noting: the one-click endpoint now **archives to Used/
only when the order reconciles**. A flagged order stays in the Awaiting queue
(logged, visible) instead of being silently filed — same "never default
silently" philosophy as the CENTROMEDIA hard-stop. Frontend holds the file
behind a confirm that names the discrepancy; "Download anyway" saves it but
still leaves the order in the queue for a manual Done once Etere is fixed.

Verified: 8 unit cases (all July error classes named correctly, incl. the
~1/(1-fee) ratio) + live end-to-end on real contract 2887 (green) and a
seeded double gross-up (red, both rates named). py_compile + JS parse clean.

Remaining pipeline: Phase 4 (customers.db contact/salesperson prefill),
Phase 5 (legacy cutover after a full clean billing cycle). Still-open
parallel-test watch item: first real H&L net-rate one-click (auto gross-up
built, never exercised live) — Phase 3 now guards exactly that case.
