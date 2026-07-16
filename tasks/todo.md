# Per-Line Language Catalog (CTV_LineLanguage) — 2026-07-16

Store the verified language for every contract line in the Etere DB at order
entry, so backwrite never has to ask. Guesses are allowed as prefills but the
user ALWAYS verifies — once written, nobody re-checks it. Long-term this closes
the last gap (commercial-log column J) blocking full live-pull from Etere.

## Plan

- [ ] **A1** Create `dbo.CTV_LineLanguage` in the Etere DB
      (ID_CONTRATTIRIGHE PK, LANG, SOURCE, UPDATED_AT; NO FK to Etere tables)
- [ ] **A2** `etere_direct_client.py`: `upsert_line_languages(cursor, rows, source)`
      + `fetch_line_languages(cursor, line_ids)` helpers
- [ ] **A3** `add_contract_line(language=...)` optional param → upsert (source='entry')
- [ ] **B1** Shared gather helper `confirm_line_languages(items)` — per-line verify,
      bracket-default prefilled with the guess, apply-to-all, NEVER silently assumed
- [ ] **B2** Wire into Daviselen + SCWA gather→automation (rest of parsers incremental)
- [ ] **C1** Backwrite EB pipeline: stored language (by Line id) overrides detection;
      detection stays as fallback for uncataloged lines
- [ ] **C2** One-click generate: modal table (already user-verified by the generate
      click) writes back to CTV_LineLanguage (source='user')
- [ ] **D1** `scripts/backfill_line_languages.py`: parse CLEANED tab of every
      Master Billing Sheet (C:\Work Temp\Billing + Miscellany archives, 2022→now),
      Line col M + Language col J, bulk upsert source='billing-book'
      (never overwrites 'entry'/'user' rows)
- [ ] **D2** Run backfill + report coverage

## Facts established

- Books: `/mnt/c/Work Temp/Billing/Master Billing Sheet 2607.xlsm` (live) +
  `Miscellany/Master Billing Sheet 26xx.xlsm` + `Miscellany/<year>/...` (2022–2025).
- CLEANED tab: header row 1; col J = Language, col M = Line (= ID_CONTRATTIRIGHE,
  verified: 74617 etc.), ~31k rows/book. Manual rows (Cornerstone/Desert/WL fees)
  have blank Line — skip.
- Language options (EB config): C, E, H, Hm, J, K, L, M, M/C, P, SA, T, V.

## Review (2026-07-16 — all items done)

- A1–A3, B1–B2, C1–C2, D1–D2 complete. Also: universal post-entry catalog pass
  in `_catalog_line_languages` (order_processing_service.py) covers every
  unwired parser, TTY-only, groups identical descriptions, never double-asks
  (skips cataloged lines). WorldLink = ALWAYS English (business rule): entry
  passes language='E'; universal pass auto-fills WL contracts silently.
- Backfill ran live: 47,087 unique lines from books 2201–2607 (2607 CLEANED
  still empty — July not billed). Distribution: E 24205, M 6676, V 5268,
  T 2804, SA 2330, C 2015, K 1598, Hm 755, M/C 656, P 497, J 243, H 27, L 13.
- Coverage vs CONTRATTIRIGHE starting 2022+: 87.3% (96–98% for 2022–24;
  2025 79% / 2026 63% = flights not yet billed). Converges via entry hook +
  backwrite writeback; script is idempotent — rerun after each billing close.
- Backfill perf note: row-by-row upsert was ~hours for 47k rows; switched to
  temp-table staging + set-based UPDATE/INSERT (~1 min). Keep
  upsert_line_languages for small entry-time writes only.
