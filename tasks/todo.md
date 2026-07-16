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

## Review

(to fill as work completes)
