# Fill & Finish — page + one-button chain (2026-08-28)

Spec: `tasks/finish-hour.md` (v0.3). Live-proven write path: `scripts/finish_apply.py`
(LAX/CVC/WDC/MMT 8/28 08:00, MMT also from bare). Lee: "you now can do everything on your own."

## Phase 1 — Fill & Finish page (Control Room, next to the Break Optimization cards)
- [ ] `src/business_logic/services/finish_service.py` — lift `scripts/finish_plan.py` +
      `scripts/finish_apply.py` into importable functions: `load_window`, `load_inventory`,
      `plan`, `apply_hour(conn, market, date, lo, hi, dry_run)`, `day_programs(cur, market, date)`
      (program windows = consecutive EVENT_TYPE='F' anchors on the broadcast day). Scripts become
      thin CLI wrappers so the CLI and the page share ONE code path.
- [ ] Route blueprint `src/web/routes/finish.py`: page `/finish`, `GET /finish/day?market&date`
      (programs list, each with remainder + derived badge), `GET /finish/plan?market&date&lo&hi`
      (timeline + edits, read-only), `POST /finish/apply` (same args; runs apply, returns AFTER).
      Register in `app.py`.
- [ ] Template `templates/finish.html` in the BO Log-Version pattern: network/market pills +
      date (auto-load, no Load button), day's programs listed log-style, expand → packed
      timeline with the remainder + planned edits, one **Finish** button per program
      (confirm), finished badge DERIVED (planner reports 0 edits + ID present). Existing CSS
      classes only (`prg-*`, `expand-btn`, `--nord4` on dark).
- [ ] Portal card next to the Break Optimization cards (before Dallas Live View, which stays last).
- [ ] Verify: page loads 8/28 for all 9 markets; badges: all finished; pick a bare hour tomorrow
      and Finish it from the page (first live click = Lee's). `uv run ruff check` clean.
- [ ] Commit + push; deploy note (Lee deploys).

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
