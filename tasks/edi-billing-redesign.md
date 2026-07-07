# EDI Billing Redesign — "PDF drop → validation screen → upload-ready ZIP"

**Written:** 2026-07-07 (planning session with Lee; spec is written to be executed
by a different model/session — be prescriptive, verify cited line numbers before
editing, they will drift.)

**Goal:** One page. Lee drops all of a month's affidavit invoice PDFs; the system
extracts contract numbers, fetches the Etere post-log CSVs itself, reconciles
totals, picks the right EDI template deterministically, and presents ONE
validation screen (peek-and-fix) → one button → ZIP of TVB EDI `.txt` files
ready for upload.

**Non-goal:** changing the EDI record format itself. Generated `.txt` output for
identical inputs must remain byte-identical (see Golden Tests).

---

## 1. Current state (verified 2026-07-07)

Two separate utilities + manual glue between them:

### `/edi/post-log` — `src/web/routes/edi.py` (530 lines) + `templates/edi/post_log.html`
- `POST /edi/post-log/parse` — upload affidavit PDFs → `_extract_contract_number`
  (edi.py:19) pulls `Contract Number NNNN` + `Affidavit <invoice-id>` from PDF page 2.
- `POST /edi/post-log/generate` — `_fetch_all_reports_sync` (edi.py:331) logs into
  Etere web ONCE (limited license seats; always `etere_web_logout` in `finally`),
  downloads report `R100018_C18236_postlog_with_contract_no` as CSV per contract
  for a user-entered date range → returns a **ZIP the user must download/unzip**.
- `POST /edi/post-log/reconcile` — user RE-uploads PDFs + CSVs → compares totals
  (`_parse_pdf_affidavit` edi.py:37 vs `_parse_csv_totals` edi.py:108).
- `POST /edi/post-log/diff` — spot-level diff PDF vs CSV (`_diff_pdf_csv` edi.py:174,
  ±10 min airtime tolerance + rate match).

### `/edi/export` — `src/web/routes/edi_export.py` (512 lines) + `templates/edi/export.html`
- User manually places CSV+PDF pairs in `incoming/EDI/` (must be named
  `MMYY-NNN…`; CSVs additionally `*_<contract>_postlog.csv`).
- `GET /edi/export/scan` — pairs files by `MMYY-NNN` prefix, parses CSV
  (`_parse_export_csv` :34) + affidavit (`_parse_affidavit_pdf` :285), suggests a
  template (`_suggest_template` :326).
- Templates: 18 JSON files in `data/edi_templates/` (CRUD at `/edi/export/templates`).
- `POST /edi/export/generate` / `/generate-batch` — build the EDI records
  (`_generate_edi` :211; record builders `_r21`…`_r34`, trailing `12;1;<gross>;`).

### Why detection mis-fires (the Toyota-for-McDonald's bug)
`_suggest_template` (edi_export.py:326):
1. Pass 1 = **exact** string equality of affidavit advertiser vs template
   `advertiser_match`. Brittle; and `_parse_affidavit_pdf` swallows ALL errors
   (`except Exception: pass` :321-322) so a layout change → empty advertiser →
   pass 1 can never match.
2. Pass 2 = any ≥3-letter word of template `agency_name` present in the CSV
   **filename**. Davis Elen has 4 templates (3 McD + 1 Toyota) sharing those
   words → first alphabetical wins regardless of advertiser.
3. Pass 3 = `templates[0]` (alphabetically `bvk_ucdavis`). Never correct on purpose.

### Known bugs in this code (from the 2026-07-07 audit — fix during this work)
- **Path traversal:** `csv_filename` from JSON body joined into `INCOMING / csv_fn`
  unsanitized in `/generate` (edi_export.py:459) and `/generate-batch` (:491).
  Reject when `Path(csv_fn).name != csv_fn`.
- **Silent failures:** `_parse_affidavit_pdf` (:321) and `_all_templates` (:244)
  swallow exceptions with no logging. Log and surface a per-row warning.
- Dead code: `_fetch_report_sync` (edi.py:319) has no callers; `_fetch_all_reports_sync`'s
  sys.path computation (edi.py:338) resolves to `src/` not repo root (works only
  because parser_bridge fixed sys.path earlier) — fix or remove while touching.

---

## 2. Key design decision: match templates by Etere customer ID, not strings

The affidavit already yields the **contract number** — the one identifier that
never varies. The contract header row holds the authoritative customer:

- `CONTRATTITESTATA.COMMITTENTE` → `ANAGRAF.ID_ANAGRAF` (customer). Verified
  pattern: `src/web/routes/airchecks.py:59`
  (`LEFT JOIN ANAGRAF a ON a.ID_ANAGRAF = ct.COMMITTENTE`).
- Agency: expect a `CONTRATTITESTATA.AGENZIA` column (customer-default agency is
  `ANAGRAF.AGENZIA`, see etere_direct_client.py:516-522). **Verify the header
  column exists** with a known contract before relying on it; if absent, customer
  ID alone is sufficient.
- ⚠ Verify once whether the affidavit's "Contract Number" equals
  `ID_CONTRATTITESTATA` or `COD_CONTRATTO`: take one real affidavit, run both
  `SELECT … WHERE ID_CONTRATTITESTATA = <n>` and `WHERE COD_CONTRATTO = '<n>'`.
  (The post-log report filter already accepts this same number, so it is almost
  certainly `ID_CONTRATTITESTATA`.)

**Template schema change** — add to each JSON in `data/edi_templates/`:
```json
"etere_customer_ids": [218, 431],   // one template can serve several ANAGRAF ids
"etere_agency_id": 203,             // optional, tie-breaker only
"market_match": "LAX"               // KEEP — tie-breaker when one customer has per-market templates
```
(Keep `advertiser_match` as a legacy fallback for brand-new customers whose
template hasn't been assigned an ID yet.)

**New matcher** (replaces `_suggest_template` as pass 0; keep old passes as
fallback, in order):
1. contract → `COMMITTENTE` (+agency) via one MSSQL query
   (`browser_automation.etere_direct_client.connect()` — see memory
   `feedback_db_query_pattern`).
2. Templates where `etere_customer_ids` contains that ID. If several, narrow by
   `market_match` against the invoice market (post-log CSV `nome2` column /
   affidavit `Market`). If still ambiguous → mark row "needs template pick",
   do NOT guess.
3. Only if no ID match: legacy advertiser/agency string passes — but flag the
   row `match_confidence: "fuzzy"` so the UI shows it amber, never silently.

**Backfill:** `scripts/backfill_edi_template_customers.py` — for each of the 18
templates, look up candidate `ID_ANAGRAF` by name (`ANAGRAF.NOME LIKE`) and by
recent contracts' `COMMITTENTE`; PRINT the proposed mapping and require a y/N
confirm before writing the JSONs (read-only against prod DB; audit rule: no
unconfirmed prod writes — this only writes local JSON but confirm anyway).

---

## 3. The EDI spec is KNOWN — validate against it, don't eyeball

The full TVB/MediaOcean Spot-Net electronic-invoice spec is transcribed in the
project memory **`reference_edi_spec.md`** ("TVB EDI Technical Spec") — record
types, field positions, max lengths, formats. Related memory
`project_edi_export.md` documents the template JSON conventions (representative
= CTV AE e.g. "Charmaine Lane"/"House"; salesperson = agency buyer;
`comment_top_by_market`). **Read both before implementing.**
(Note: `.claude/documents/tvb_xml_schemas/` is the *XML* proposal/order family —
a different, future interchange format. The invoice `.txt` this tool generates
is the positional semicolon format below. Don't conflate them.)

The validation screen must enforce, per field (hard error = blocks export;
warn = amber):

| Rule | Source |
|---|---|
| `advertiser_name`, `product_name`, `agency_name`, representative, salesperson ≤ 25 chars | R21 f2 / R31 f1-f4 |
| `agency_ad_code`, `agency_prod_code` ≤ 8 chars | R31 f24/f26 (strongly recommended non-empty) |
| Address lines ≤ 30 chars; agency EDI code ≤ 8; call letters = 4 | R21/R22/R23 |
| `invoice_date`, period/schedule/contract dates = 6-digit YYMMDD; `broadcast_month` = 4-digit YYMM | R31 f5, f9-f15 |
| `invoice_number` ≤ 10; `estimate_code` ≤ 10; rep/station order numbers ≤ 10 | R31 f7/f8/f21/f22 |
| Comments (R32/R33) ≤ 130 chars each | R32/R33 f1 |
| Money = integer cents, no separators, `-` prefix if negative; R34 gross/commission/net must satisfy gross − commission = net; R34 f12 = spot count | R34, dollar rules |
| R51: run date YYMMDD, time HHMM military, length seconds, copy_id ≤ 30, rate cents | R51 |
| Required records present: 21, 22, 23, 31, ≥1×51, 34, 12 | record table |

Known accepted omissions in current output (validated clean by TVInvoices
May 2026): R51 f3 DAY_OF_WEEK (Mon=1…Sun=7) left empty; R22 station
name/address empty. **Do not "fix" these silently — byte-identical golden rule.**
If ever needed, derive day-of-week from run_date behind an explicit option.

Upload to tvinvoices.com stays manual for now (the future step-4 automation in
`project_edi_export.md` is out of scope here).

## 4. Target flow (one page, `/edi/billing`)

```
[Drop zone: all affidavit PDFs for the month]
        ↓ POST /edi/billing/intake          (multipart)
  per PDF: extract invoice_id + contract_no (reuse _extract_contract_number),
  totals (reuse _parse_pdf_affidavit), header fields (reuse _parse_affidavit_pdf),
  save the PDF into incoming/EDI/, derive broadcast month from invoice prefix
        ↓ table of rows appears immediately (no Etere touched yet)
[Fetch post logs] button  ← deliberate button, NOT automatic: the fetch holds an
        ↓                    Etere license seat; one shared session for the batch
  POST /edi/billing/fetch  → _fetch_all_reports_sync into incoming/EDI/
  (async via asyncio.to_thread; per-row status streamed or polled)
        ↓ rows fill in: CSV totals, reconcile badge, template match
[VALIDATION SCREEN — the peek-and-fix]
  per row: 🟢 spots+gross match / 🔴 mismatch (click → spot-level diff modal,
  reuse _diff_pdf_csv) / 🟡 fuzzy template match or missing fields.
  Template dropdown (override), all EDI invoice fields editable
  (reuse export.html's editors). Export blocked until every row is green
  or explicitly checked "export anyway".
        ↓ POST /edi/billing/export (selected rows)
[ZIP of .txt files ready for upload]
```

Date range for the fetch: derive from the invoice-number `MMYY` prefix
(`_invoice_info` edi_export.py:258 already does this). **Default to the
broadcast month** (Mon of week containing the 1st → Sun of week containing the
month's last day — helpers exist in `src/backwrite/transformer.py`
`compute_broadcast_month` area), but show the range in an editable field before
fetch. ASK LEE at first demo whether billing wants broadcast or calendar month;
current tool makes him type it manually so there is no precedent in code.

---

## 5. Implementation plan (ordered; each phase shippable)

### Phase 0 — Golden tests + safety fixes (do FIRST, no behavior change)
- [ ] Capture golden outputs: for 2–3 real invoice CSV/PDF pairs (ask Lee for a
      recent month, or use any pair already in `incoming/EDI/`), run today's
      `/edi/export/generate` and commit the resulting `.txt` under
      `tests/fixtures/edi_golden/`. Add a unit test that `_generate_edi` with the
      same template+inv+spots reproduces them byte-for-byte.
- [ ] Fix path traversal in `/generate` + `/generate-batch` (reject
      `Path(csv_fn).name != csv_fn`).
- [ ] Replace the two silent `except Exception: pass` with logged warnings that
      propagate a `warnings: []` list to the API response.
- [ ] Delete dead `_fetch_report_sync`; fix the sys.path root in
      `_fetch_all_reports_sync` (or move it, Phase 2).

### Phase 1 — Consolidate the duplicated logic into a service module
Create `src/business_logic/services/edi_billing.py`; MOVE (don't copy):
- [ ] Affidavit parsing: merge `edi.py:_parse_pdf_affidavit` +
      `edi_export.py:_parse_affidavit_pdf` into one `parse_affidavit(pdf_bytes) -> AffidavitData`
      (dataclass: invoice_id, contract_no, advertiser, market, total_spots,
      gross_amount, rep_order_number, agency_ad_code, agency_prod_code,
      product_name, comment_top, comment_bottom, warnings).
- [ ] CSV parsing: merge `_parse_export_csv` (spots) + `_parse_csv_totals`
      (totals) — one parser returning both (totals = derived from spots; keep the
      last-line totals read as a cross-check, mismatch → warning).
- [ ] EDI generation (`_generate_edi` + `_r*` builders) — move verbatim
      (golden test must still pass).
- [ ] Template store helpers + the new customer-ID matcher (section 2).
- [ ] Routes in `edi.py` / `edi_export.py` become thin wrappers; existing pages
      keep working unchanged.

### Phase 2 — Template customer-ID matching
- [ ] Add matcher + schema fields (section 2). Extend the template editor UI
      (export.html template modal) with `etere_customer_ids` /
      `etere_agency_id` inputs.
- [ ] `scripts/backfill_edi_template_customers.py` with confirm gate; run it
      with Lee, commit the updated 18 JSONs.
- [ ] Wire matcher into the existing `/edi/export/scan` immediately (drop-in
      improvement even before the new page exists) with `match_confidence` in
      the response; UI shows amber for fuzzy.

### Phase 3 — The unified `/edi/billing` page
- [ ] `POST /edi/billing/intake` — accept PDF uploads; parse; write PDFs into
      `incoming/EDI/` (sanitize filenames!); return row objects.
- [ ] `POST /edi/billing/fetch` — body: rows + date range; single Etere session
      (reuse `_fetch_all_reports_sync`, moved into the service); write CSVs into
      `incoming/EDI/` named `<invoiceprefix>_<contract>_postlog.csv`; return
      per-row success/error. Keep `finally: etere_web_logout` — a leaked seat
      locks the account (see CLAUDE.md/data-reference logout rule).
- [ ] Row assembly endpoint (or fold into fetch response): CSV totals + spot
      parse + reconcile flags + template match + prefilled invoice fields —
      i.e. today's `/scan` result + reconcile in one object.
- [ ] `POST /edi/billing/diff` — thin wrapper over existing `_diff_pdf_csv`,
      reading both files from `incoming/EDI/` (no re-upload).
- [ ] Field validators from the spec table in section 3 — one
      `validate_invoice(template, inv, spots) -> list[Issue]` in the service
      module, used by BOTH the UI (live, per-field) and the export endpoint
      (server-side gate). Issue = {field, level: error|warn, message}.
- [ ] `POST /edi/billing/export` — same as `/generate-batch` but takes row
      selection + the edited fields, refuses rows that are red (reconcile
      mismatch OR validation errors) unless `force: true` per row.
- [ ] Page `templates/edi/billing.html` — start from `export.html` (its field
      editors, char counters, template modal are reusable); add drop zone,
      fetch button + progress, reconcile badges, diff modal, export gating.
      UI rules: existing CSS classes only (memory `feedback_ui_styling`), 🏠
      home button not back-arrow (memory `feedback_home_button`).

### Phase 4 — Cutover
- [ ] Nav: point the EDI menu entry at `/edi/billing`.
- [ ] Keep `/edi/post-log` + `/edi/export` for one real billing cycle as
      fallback, then delete their pages (routes' logic already lives in the
      service module).

## Verify (definition of done)
- [ ] Golden `.txt` outputs byte-identical through Phases 1–3.
- [ ] Real-month test with Lee: drop the month's PDFs; every McDonald's invoice
      matches a McD template and every Toyota invoice a Toyota template
      (the original bug); reconcile catches at least one known mismatch if one
      exists; export ZIP uploads clean to the EDI portal.
- [ ] A PDF whose contract has NO template → row flagged, not mis-assigned.
- [ ] Fetch failure mid-batch (kill network) → session logged out, partial rows
      show errors, retry re-fetches only failed rows.
- [ ] 280-test suite still green; new unit tests for: matcher (ID hit, market
      tie-break, ambiguous → no-guess, fuzzy fallback flag), affidavit parser on
      2+ real PDFs, CSV parser totals cross-check.

## Open questions for Lee (ask at first demo, don't block)
1. Fetch range: broadcast month vs calendar month? (Default: broadcast.)
2. Should intake reject PDFs whose invoice `MMYY` differs from the batch's
   month, or just flag them?
3. Post-cutover: is spot-level diff still needed as a standalone tool, or only
   as the red-badge drill-down?
