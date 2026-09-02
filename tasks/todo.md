# ⏸ PAUSED — Agency Pre/Post Logs — third card on /scripts/reportsort (2026-09-02)

**Status (Lee, 2026-09-02): WAIT.** Lee is asking Aki exactly what she needs before anything is
built — she may need only AMOUNTS (per-contract/per-month totals), not every individual spot.
If it is amounts, the answer is a revenue/summary query, not ReportSort at all. Do NOT start
the plan below until Lee relays Aki's answer.

Ask (Lee, via Aki): pick an agency, enter dates, get a Pre/Post log for EVERY contract of
that agency in the range, using the same CTV/TAC templates as the Worldlink card. Aki's
"WL consolidated logs for the year" attempt failed in the billing tool — that tool is not
for this. WL 2026 YTD = ~246K placed spots / 152 contracts, so a single year-long Etere
report call is the likely failure mode; the new card must chunk the pull.

## Plan
- [ ] `GET /api/scripts/reportsort/agencies` — `SELECT a.ID_ANAGRAF, a.RAG_SOCIAL, COUNT(*)`
      from CONTRATTITESTATA JOIN ANAGRAF ON AGENZIA, ordered by name; only agencies that
      own contracts. Feeds a searchable dropdown (same `.search-dropdown` pattern as the
      contract card, data-idx delegation, never inline onclick).
- [ ] `scripts/run_reportsort.py` agency mode: `--agency-id N --output-folder DIR`.
      - Pull the placement-confirmation report **per calendar month** inside the range
        (`filters[1]=agency`, `agencyid=agency`, contract blank) and concatenate the CSVs
        (keep rows 0–3 of the first chunk only). Etere has a ~70 s fixed cost per call, so a
        year ≈ 12 calls ≈ 15–20 min — print `[INFO] chunk k/n` so the terminal shows progress.
      - Query CONTRATTITESTATA for the agency's COD_CONTRATTO values overlapping the range
        and pass them to ReportSort as the exact allow-list (75 agency contracts have codes
        that do not start with a letter, e.g. `3Fold LRCC 2611` — the WL heuristics would
        silently drop them). Footer rows fall out for free, same as single mode.
      - Default output = per-run temp dir (browser download), NOT `K:\!Archives` — the WL
        card keeps that path untouched.
- [ ] `ReportSort/main.py` (separate repo, own commit): `--only-booking` becomes repeatable
      (`action="append"` → set membership). Single-contract callers pass one value and behave
      exactly as today; WL batch passes none and keeps the heuristics byte-for-byte.
- [ ] Route `run_reportsort`: accept `agency_id`; reuse the token/temp-dir mechanics; after
      exit 0, zip every `*.xlsx` in the folder into `<Agency>_<pre|post>_<from>-<to>.zip` and
      emit ONE `[DOWNLOAD:token|zip]` line (150 individual links is not a UI). Keep the
      per-file lines too so a small pull still shows the workbooks.
- [ ] Template: third `.tool-card` titled **Agency Pre/Post Logs** — agency search, log type,
      from/to, Run, terminal, download row. Existing classes only; `formatDateInput` from the
      injected date helper.
- [ ] Verify: (1) WL regression oracle — run the old and new `main.py` over the same archived
      WL CSV into two folders, diff every workbook cell-by-cell, expect 0 diffs;
      (2) agency pull for a small agency over one month end-to-end from the page → zip opens,
      one workbook per contract, TAC template only when Dallas rows present;
      (3) a 2-month range crosses a chunk boundary → row count equals the sum of the two
      single-month pulls, no duplicated header rows; (4) `uv run ruff check` clean.
- [ ] Commit + push both repos; note for Lee: Aki should pull a quarter at a time until we
      have measured a full-year run.

## Open question for Lee
- Chunk = calendar month or broadcast month? Calendar is simpler and invisible in the output
  (ReportSort re-sorts every booking by market/date). Going with calendar unless told otherwise.

---

# Fill & Finish — page + one-button chain (2026-08-28)

Spec: `tasks/finish-hour.md` (v0.3). Live-proven write path: `scripts/finish_apply.py`
(LAX/CVC/WDC/MMT 8/28 08:00, MMT also from bare). Lee: "you now can do everything on your own."

## Phase 1 — Fill & Finish page (Control Room, next to the Break Optimization cards)
- [x] `src/business_logic/services/finish_service.py` — lift `scripts/finish_plan.py` +
      `scripts/finish_apply.py` into importable functions: `load_window`, `load_inventory`,
      `plan`, `apply_hour(conn, market, date, lo, hi, dry_run)`, `day_programs(cur, market, date)`
      (program windows = consecutive EVENT_TYPE='F' anchors on the broadcast day). Scripts become
      thin CLI wrappers so the CLI and the page share ONE code path.
- [x] Route blueprint `src/web/routes/finish.py`: page `/finish`, `GET /finish/day?market&date`
      (programs list, each with remainder + derived badge), `GET /finish/plan?market&date&lo&hi`
      (timeline + edits, read-only), `POST /finish/apply` (same args; runs apply, returns AFTER).
      Register in `app.py`.
- [x] Template `templates/finish.html` in the BO Log-Version pattern: network/market pills +
      date (auto-load, no Load button), day's programs listed log-style, expand → packed
      timeline with the remainder + planned edits, one **Finish** button per program
      (confirm), finished badge DERIVED (planner reports 0 edits + ID present). Existing CSS
      classes only (`prg-*`, `expand-btn`, `--nord4` on dark).
- [x] Portal card next to the Break Optimization cards (before Dallas Live View, which stays last).
- [~] Verify: endpoints + page tested locally on MMT 8/28 (page/card/day/plan/400); first live click from the page = Lee's. Remaining: page loads 8/28 for all 9 markets; badges: all finished; pick a bare hour tomorrow
      and Finish it from the page (first live click = Lee's). `uv run ruff check` clean.
- [x] Commit + push; deploy note (Lee deploys).

## Phase 2 — one-button chain from Daily Programming ("set up Korean News → everything")
Order per market, stop-on-problem, report per step:
1. Daily Programming placement (exists: `daily_programming_run.run_market`)
2. Fill & Finish per program window touched (Phase 1 service)
3. Break Optimization bulk-apply on those windows (exists: `/break-optimization/bulk-apply`) —
   separation check; later: automated relocation (spot relocator design, `tasks/spot-relocator.md`)
4. Log time fill (exists: `_mc_fill_program_spots` / traffic log-sync)
5. Publish stays with the operator for now; auto-publish switch later (Lee 8/28)
- [ ] Design doc `tasks/finish-chain.md` before code: idempotency per step, failure surface,
      what "done" looks like on the Daily Programming page.

## Open items carried
- COMS block target lengths as the evenness source (planner still shortest-first).
- Publish: operator, for now.
