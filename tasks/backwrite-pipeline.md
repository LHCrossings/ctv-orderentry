# Backwrite Pipeline — "order entry → awaiting backwrite → one-click Excel"

**Written:** 2026-07-10 (planning session with Lee; spec written to be executed by a
different model/session — be prescriptive, verify cited line numbers before editing,
they will drift.)

**Goal:** An order entered through the order-entry utility stays in the utility in an
"Awaiting Backwrite" state, carrying a machine-written manifest of everything answered
at entry time. After the human steps in Etere (approve → scheduler → traffic), the user
clicks **Backwrite** on that order and the Excel pops out — zero re-keyed fields.

**Why:** July 2026 booked-business reconciliation found every discrepancy was a
LOCAL-side human error made at backwrite time, re-answering questions that order entry
had already answered correctly:
- Billing type (Broadcast/Calendar) carried over from a dragged-in template instead of
  the contract's CENTROMEDIA (McDonald's, Sky River — ~$5,200 net misattributed).
- Manual gross-up applied to rates that were already gross (Daviselen — $1,164 net).
Etere + the IO parsers were right in every single case. The fix is to stop asking the
human at backwrite what the system already knew at entry.

**Rollout (per Lee, same as the EDI billing cutover):** build the new flow alongside the
existing utilities. The current `/backwrite` card stays available, marked **(legacy)**
with a `Legacy` badge (precedent: `templates/billing.html:110-122`). Both run in
parallel during testing; when the new flow is proven over a full billing cycle, delete
the legacy cards.

**Non-goals:** changing the backwrite Excel format (output for identical inputs stays
identical — the transformers are not touched); changing the Etere-side steps (approve /
scheduler / traffic remain manual by design — Lee wants approval kept as a double-check).

---

## 1. Current state (verified 2026-07-10)

### Order queue — file-based, `src/web/routes/orders.py`
- Pending IOs live in `config.incoming_dir`; `GET /api/orders` → `_scan_dir` (orders.py:1584-1588).
- "Done" today = `DELETE /api/orders/{filename}` → moves file to `incoming/Used/`
  (`move_to_used`, orders.py:1610-1645; `used_dir` defined at orders.py:373).
- Used files listed via `_scan_dir(used_dir)` (orders.py:1794).
- Entry itself runs through the run console / parser bridge;
  `OrderProcessingService._enrich_results()` already resolves each returned contract
  code → Etere DB id (`CONTRATTITESTATA.COD_CONTRATTO`) after every batch.

### Backwrite page — `src/web/routes/backwrite.py`
- `POST /backwrite/generate` (backwrite.py:417-447) takes the hand-maintained fields
  that caused the July errors: `billing_type` (free Form field), `agency_fee` (typed,
  default 15), `gross_up_rates` (manual per-rate JSON dict), plus salesperson/contact
  block re-typed every time.
- The dragged IO is parsed via `web.parser_bridge.get_order_detail` (backwrite.py:517)
  but only mined for conveniences, and a parse failure just prints and continues
  (backwrite.py:~527) — silent fallback to human memory.
- `POST /backwrite/fetch-report` (backwrite.py:587+) already pulls the Etere
  commercial-log CSV for a contract. `POST /backwrite/preview-from-db` (:280) already
  generates from a contract id.
- WorldLink has its own flow (`/backwrite/worldlink/*`, backwrite.py:129-278) with
  revision merge (`read_sc_lines_from_excel` / `merge_revision_lines` in
  `src/backwrite/worldlink_transformer.py`).

### Key invariants this design leans on (verified against live DB 2026-07-10)
- **Etere `CONTRATTIRIGHE.IMPORTO` is ALWAYS the gross rate.** Parsers gross up
  net-rate IOs at entry (H&L, Admerasia); gross-rate IOs enter as-is (Daviselen).
- For net-rate orders the EXACT dollar figure is the IO's net; Etere stores
  round(net / (1−fee), 2). Exact net is recoverable as round(IMPORTO × (1−fee), 2),
  but the manifest should carry the IO's own rates so nothing is derived.
- Billing type per contract = `CONTRATTITESTATA.CENTROMEDIA` (316 Broadcast /
  317 Calendar / 0-NULL unset). Agency % = `CONTRATTITESTATA.P_AGENZIA`.
- Every parser's order object carries `rates_are_net: bool` (mandated by
  tasks/lessons.md) — currently discarded after entry.
- **The backwrite Excel mimics the IO's line structure, never Etere's** (Lee,
  2026-07-10). Etere splits lines for its own scheduling (week consolidation, market
  fan-out); the client-facing Excel must read like the IO the agency wrote. The
  manifest therefore stores lines AS THE PARSER SAW THEM on the IO (that's also why
  the IO file is kept alongside the manifest — human-readable source for any dispute).
  Etere's line structure is the fallback source of truth ONLY when no IO/manifest
  exists and the IO layout cannot be replicated (legacy contracts, manual entries).
  This matches the existing "Backwrite SC Lines — group by (desc, rate)" lesson.

---

## 2. Design

### 2.1 Manifest written at entry (Phase 0)
When `OrderProcessingService` finishes a successful order (right after
`_enrich_results`), serialize a **backwrite manifest** JSON next to the IO:

```
incoming/Entered/<io-filename>                    ← the IO file, moved here on success
incoming/Entered/<io-filename>.manifest.json      ← the manifest
```

Manifest contents (one file per IO; `contracts` is a list — see multi-contract below):

```json
{
  "manifest_version": 1,
  "io_filename": "...", "order_type": "hl", "entered_at": "2026-07-10T21:15:00",
  "rates_are_net": true,
  "agency_fee_pct": 15.0,
  "contracts": [
    {"code": "HL NorCal 2607", "etere_id": 2871, "estimate": "1460", "revision": 0,
     "lines": [
       {"line": 1, "description": "M-F 8-9p Mandarin News", "market": "SFO",
        "rate_as_ordered": 55.00, "spots": 6, "start": "2026-06-29", "end": "2026-07-26",
        "is_bonus": false}
     ]}
  ],
  "user_inputs": { "...the gathered dict verbatim (contract_code, customer_id, billing_type, separation, ...)" }
}
```

Serialization is generic: `dataclasses.asdict` on the parsed order where possible,
`default=str` fallback — parsers differ, the manifest tolerates extra keys. Never let a
manifest failure fail the entry itself (log loudly, leave file in incoming/ root).

### 2.2 "Awaiting Backwrite" queue state (Phase 1)
- New subdir `incoming/Entered/` (sibling of `Used/`). `_scan_dir` on the root already
  excludes subdirs, so the main queue stays clean automatically.
- Orders page gets an **Awaiting Backwrite** section: one card per manifest contract
  (order file + contract code + entered date + Etere id). Existing pending cards keep
  their current buttons; the delete→Used path remains for never-entered files.
- On successful entry the file+manifest move to `Entered/` automatically. If an entry
  succeeded but the move fails (file open on Windows — see move_to_used's 409 handling),
  surface the same "close the PDF viewer" message; manifest still written.

### 2.3 One-click backwrite (Phase 2)
`POST /api/orders/backwrite/{manifest-contract}`:
1. Fetch the Etere commercial-log CSV for the contract id (reuse the internals of
   `/backwrite/fetch-report`).
2. Query live `CENTROMEDIA` + `P_AGENZIA` for the contract → billing_type + agency_fee.
   CENTROMEDIA unset (0/NULL) → hard stop with a "set billing type on the contract"
   message (same philosophy as the booked-business unset list). Never default silently.
3. Build `gross_up_rates` from the manifest: `rates_are_net=false` → empty;
   `rates_are_net=true` → per-line exact ordered rates from the manifest.
4. Salesperson / contact block / emails / address from `customers.db` (see 2.6).
5. Call the existing generate path (`backwrite.py` `/generate` internals refactored into
   a callable) with those inputs. Return the Excel. On success move IO+manifest to `Used/`.
6. WorldLink order types route to the existing WorldLink flow prefilled from the
   manifest instead (contract number, revision) — its transformer already handles the
   rest, including revisions.

### 2.4 Reconciliation banner (Phase 3 — subsumes deferred item #12)
Before returning the Excel, compare three ways and render a banner in the response:
- **Manifest lines vs Etere CSV**: per-line rate ratio ≈ 1/(1−fee) (≈1.1765 at 15%) →
  "gross-up disagreement"; spot-count delta → "revision or entry gap"; missing line →
  named.
- **Excel totals vs Etere** Σ(IMPORTO × spots): must match to the cent for gross-rate
  orders, to the manifest's exact rates for net-rate.
Green banner = numbers proven. Red banner = generation still allowed (download button
behind an explicit "generate anyway") but the discrepancy is named and logged.

### 2.5 Revisions
A revision IO entered through order entry writes its own manifest. When its contract
code matches an existing `Entered/` manifest, the new manifest **supersedes** it (old
one renamed `.superseded-<ts>.json`, kept for audit). The Awaiting card shows the
revision number. Full line state lives in each manifest (never deltas — the 2026-07-10
WorldLink weeks bug came from losing carried-over line state).

### 2.6 Customer-stable fields (last manual re-key)
Add to `customers.db` via `_migrate_schema()` (backward-compatible, precedent in
`customer_repository.py`): `sales_person`, `contact_person`, `phone`, `fax`,
`email_1..4`, `address`, `city`, `state`, `zip`, `revenue_type`, `affidavit_flag`.
Editable in the existing customers UI. Backwrite prefill reads them; gather functions
may upsert when they learn something new.

---

## 3. Phases (each independently shippable; verify before moving on)

- **Phase 0 — manifest write.** Purely additive. Verify: enter a real order end-to-end,
  inspect the JSON, confirm entry behavior unchanged and a manifest failure cannot fail
  an entry. **DONE 2026-07-10** (`backwrite_manifest.py`; hooked into both batch paths of
  `order_processing_service.py`; verified against the real Daviselen Est 1460 IO —
  rates_are_net=False, 4 IO lines / $1,400 / 16 spots captured; empty-parse and
  exception paths verified non-fatal). Known gap for Phase 2: `parser_bridge`
  `_normalize_line` returns empty start/end dates for some parsers (e.g. Daviselen) —
  Phase 2 must extend normalization (or read the parser's own week fields in
  `io_detail`) before it can build flight-dated Excel lines.
- **Phase 1 — Entered/ state + Awaiting Backwrite UI.** Verify: entered order disappears
  from pending, appears in Awaiting with correct contract(s); never-entered files still
  delete to Used. **DONE 2026-07-10.** Entry auto-moves IO+manifest to `incoming/Entered/`
  (locked files self-heal via a sweep on every queue load); orders page gained an
  "Awaiting Backwrite" tab (rows show contracts + Etere IDs + entered date, detail modal
  works from Entered/, `IO?` badge on parse-failed manifests); each row has a disabled
  Backwrite button (Phase 2) and a **Done** button that archives IO+manifest to `Used/`
  — the manual escape hatch for the legacy-parallel period. Endpoints:
  `GET /api/orders/awaiting-backwrite`, `POST /api/orders/awaiting-backwrite/{file}/done`.
  Verified end-to-end via TestClient (7 checks incl. stray sweep and Used archive).
- **Phase 2 — one-click backwrite.** Verify against a real contract: generated Excel is
  cell-identical to one produced through the legacy page with correct manual inputs
  (H&L net-rate case AND Daviselen gross-rate case AND one WorldLink order).
- **Phase 3 — reconciliation banner.** Verify: seed a deliberate mismatch (edit a rate in
  a manifest copy) → red banner names the ratio; real order → green.
- **Phase 4 — customers.db fields + prefill.** Verify: contact block appears without typing.
- **Phase 5 — legacy cutover.** Mark the `/backwrite` card "(legacy)" + Legacy badge.
  After a full billing cycle of parallel use with no divergence: delete the legacy cards
  (keep routes one release longer, then remove).

---

## 4. Open items / decisions already made

- Approval, scheduler, traffic stay manual in Etere (Lee: approval is a wanted
  double-check). The Awaiting state simply spans them.
- Manifest lives as JSON sidecar (transparent, greppable, survives app restarts, no new
  DB). If it ever needs querying at scale, migrate to sqlite then — not now.
- Multi-contract IOs (H&L per-estimate, Impact per-quarter, WorldLink batches): one
  manifest, one Awaiting card per contract, backwrite per contract.
- Related deferred items: #11 (EtereBridge reads CENTROMEDIA — separate repo, separate
  effort), #12 (folded into Phase 3 here), backlog #4/#5 (production/programmer fees —
  unchanged, still outside Etere).
