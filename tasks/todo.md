# ⏳ ACTIVE — EDL import: GAP (omit) markers (2026-09-04)

Lee: NEWSTODAY episodes sometimes drop an internal section; EDIUS export marks it as two
adjacent point markers commented `GAP` (first = in, second = out). Today's file
`/mnt/c/Work Temp/!New/!Orders/MBC.csv` → asset NEWSTODAY090426 (148268). Oracle for the
omit row shape = NEWSTODAY040926 (135045): FINTERRUZIONI MARKIN<MARKOUT, BULK_VIDEO=1,
INSERTION_POINT=0, FLAG=''; explode keeps MARKIN, resumes at MARKOUT+1; DURATION/DURATA =
EOM − Σ(MARKOUT−MARKIN).

## Follow-up: Finish NYC 9/4 08:00 "packed end changed after break optimization" (2026-09-04)
- [x] window_from_day: paid spot past `hi` is this hour's when an F anchor exists at `hi` (Redfin :15 case)
- [x] plan_window: overage (remainder < 5 s with fill kept) → auto-refill (Lee: strip ALL PI/PSA, refill); overrun judged on program+paid only; error text for overrun
- [x] mmss negative fix; packed_remainder shared helper; tests/unit/test_finish_plan_window.py
- [x] dry-run NYC 08:00: 8 PIs stripped, 7 inserts, ends 09:00:12.78, BO 0 changes
- [x] Lee clicked Finish on NYC 9/4 08:00 — live overage refill worked (hour reads finished, ID airs 12.3 s)
- [x] overrun (program+paid alone spill) → strip-only plan stays writable (`strip_only`, 'Strip fill' button); refuses only with nothing left to strip

## Plan
- [x] `parse_edius_csv` → `(splits, eom, omits)`; GAP markers must pair adjacently, never last, never zero-length (ValueError otherwise)
- [x] `expected_parts(splits, eom, omits)` reproduces the 040926 explode plan exactly
- [x] `apply_edl_from_csv(..., omits=)` writes BULK_VIDEO rows per VERSION, DURATION/DURATA net of the gaps
- [x] both routes pass omits; `count` = len(parts); message names gaps
- [x] tests/unit/test_edl_import.py (MBC.csv shape, oracle plan, refusals, SQL capture)
- [x] live dry-run on 148268 + 135045 (oracle V0 header + explode plan reproduced exactly; 50/60fps rows ±1 frame rounding)
- [ ] live COMMIT on 148268 — CLI write blocked by the permission classifier; Lee drops MBC.csv on /scripts/import-edl (or approves the CLI write)
- [x] commit, push, post_push.sh, lesson

# ⏳ ACTIVE — MVMS (Marathon Ventures) WorldLink post-log export (2026-09-02)

Aki forwarded Marathon Ventures' data request: spot-level "post log" of aired spots, Q1 2025 →
Q3 2026, in their 17-column template (`/mnt/c/Work Temp/!New/!Orders/MVMS Data Request - SAMPLE
Crossings TV.xlsx`). Lee's decisions (9/2): **WorldLink agency (ANAGRAF 133) only; COM + BNS
only (no PER); "Agency" = the client agency (Tatari, Marketing Architects, Direct Donor…) parsed
from the client name's parenthetical, plus a Rep column = Worldlink; 800 Number stays blank
(not in Etere); one xlsx per quarter saved to K: for Aki; one-off script kept in the repo.**
Aired = `TPALINSE.STATUS='Q'` (A = aborted, never aired — see ee-status memory).

## Plan
- [x] `scripts/mvms_post_log_export.py --from 2025-01-01 --to 2026-09-30 --out-dir "<K:>"`
      (defaults: agency 133, types COM,BNS). Per calendar quarter: query TPALINSE Q rows →
      trafficPalinse → CONTRATTIRIGHE → CONTRATTITESTATA(AGENZIA=133) → ANAGRAF ×2 → FILMATI;
      enclosing PGM = latest PGM row on the same market/day with ORA ≤ spot ORA.
- [x] Derivations: post-midnight ORA ≥ 24h → next calendar date, time − 24h; Length `m:ss`;
      Rate = CONTRATTIRIGHE.IMPORTO (BNS → 0.00); Daypart code from the LINE window
      (EM 6-9a / DA 9a-6p per Marathon's sample / PR 6-11p / LF 11p-2a / ON 2-6a; ROS when the
      window spans ≥ 3 codes). Lee 9/2: Rate = as entered in Etere (unit price on the NYC line,
      0.00 on the other 8 market lines of the same order line), NO extra unit-rate column.; Time Period `(h:mm:ss AM-…)`;
      Agency alias map (MA→Marketing Architects, DD/Direct→Direct Donor, Tatari→Tatari Inc,
      Icon/IMD→Icon Media Direct, Inc., KCLL→Key Contacts - Legal Leads),
      Advertiser = client name minus the parenthetical.
- [x] Workbook per quarter: Marathon's 17 columns in their order + extras at the right
      (Rep, Spot Type, Market, Line Descr); a Summary sheet (rows per market/month/agency).
- [~] Verify: per-quarter row count == direct COUNT(*) of the same filter; distinct parsed
      agency list printed for Lee; spot-check 3 rows against EE; post-midnight rows land on the
      next date; every file < 1,048,576 rows; `uv run ruff check` clean.
- [ ] Commit + push; tell Lee the K: folder; note for Aki: 800 Number blank by design.

---

# ✅ DONE — EDI R34 commission = EDI gross − affidavit net (2026-09-02)

Lee: TVInvoices only carries 2-decimal spot rates, so the EDI gross drifts from our affidavit
gross (fractional-cent gross-up rates). We cannot change the R51 rates, but R34 takes the
commission in DOLLARS — so set commission = EDI gross − our affidavit NET, and the EDI net
equals our invoice to the penny. Only the commission differs from the affidavit's 15% line.

Evidence: 2606-042 EDI gross 6,588.40 / 15% 988.26 / net 5,600.14 vs our net 5,600.00 →
commission must be 988.40. 2605-054: 3,294.20 / 494.13 / 2,800.07 vs 2,800.00 → 494.20.

- [x] `parse_affidavit`: capture `Net Amount Due $ X` → `AffidavitData.net_amount` and
      `Agency Commission 15% $ Y` → `commission_amount` (display only).
- [x] `_r34(t, gross, count, net_cents=None)`: when `net_cents` given → comm = gross − net_cents;
      else today's round(gross × pct). Guard: only apply when |comm − pct-based| ≤ spot_count × 1¢
      (same tolerance family as the 'rounding' reconcile badge); beyond that fall back to pct and
      raise a validation WARN — never invent a large commission delta silently.
- [x] Export route: recompute `net_cents` server-side from the paired affidavit (like the reconcile
      gate), never trust the client. `generate_edi` reads `inv["net_cents"]`.
- [x] Row assembly + billing.html: show "EDI commission $988.40 (affidavit $988.24, +$0.16) → net
      $5,600.00 ✓" in the rounding badge detail so Lee sees what will be uploaded.
- [x] Tests: unit test on _r34 (override, guard, fallback); goldens 2606-009/016/058 must stay
      byte-identical (their 15% is exact, no net override in the fixture → unchanged); add
      2606-042 affidavit as a parse fixture (net 5,600.00, commission 988.24).
- [x] ruff clean, commit, push. Windows repo pulls (Lee).

**Review:** live June batch (15 rows) re-assembled: 14 rows delta 0¢ (exact 15% already),
only 2606-042 changes → R34 `658840;98840;560000` = net $5,600.00. Goldens byte-identical.
Found + fixed on the way: the affidavit renderer splits digits in the summary figure
(`$ 3 ,803.75`, `$ 9 99.94`) — regex accepts spaces up to the cents; the guard had correctly
refused those three rows while the parse was wrong (fallback to 15% + amber warning).

---

# ✅ DONE — Agency Post Logs — third card on /scripts/reportsort (2026-09-07)

**Status (Lee, 2026-09-07):** Aki wants to pull post logs BY AGENCY in future; the MVMS
spreadsheet stays as delivered. Lee chose the ReportSort per-contract workbooks (CTV/TAC
templates), POST only. Built 9/7 — plan below ticked as shipped.

## Plan
- [x] `GET /api/scripts/reportsort/agencies` — `SELECT a.ID_ANAGRAF, a.RAG_SOCIAL, COUNT(*)`
      from CONTRATTITESTATA JOIN ANAGRAF ON AGENZIA, ordered by name; only agencies that
      own contracts. Feeds a searchable dropdown (same `.search-dropdown` pattern as the
      contract card, data-idx delegation, never inline onclick).
- [x] `scripts/run_reportsort.py` agency mode: `--agency-id N --output-folder DIR`.
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
- [x] `ReportSort/main.py` (separate repo, own commit): `--only-booking` becomes repeatable
      (`action="append"` → set membership). Single-contract callers pass one value and behave
      exactly as today; WL batch passes none and keeps the heuristics byte-for-byte.
- [x] Route `run_reportsort`: accept `agency_id`; reuse the token/temp-dir mechanics; after
      exit 0, zip every `*.xlsx` in the folder into `<Agency>_<pre|post>_<from>-<to>.zip` and
      emit ONE `[DOWNLOAD:token|zip]` line (150 individual links is not a UI). Keep the
      per-file lines too so a small pull still shows the workbooks.
- [x] Template: third `.tool-card` titled **Agency Post Logs** — agency search, (post only, no log type),
      from/to, Run, terminal, download row. Existing classes only; `formatDateInput` from the
      injected date helper.
- [x] Verify: (1) WL regression oracle — run the old and new `main.py` over the same archived
      WL CSV into two folders, diff every workbook cell-by-cell, expect 0 diffs;
      (2) agency pull for a small agency over one month end-to-end from the page → zip opens,
      one workbook per contract, TAC template only when Dallas rows present;
      (3) a 2-month range crosses a chunk boundary → row count equals the sum of the two
      single-month pulls, no duplicated header rows; (4) `uv run ruff check` clean.
- [x] Commit + push both repos; note for Lee: Aki should pull a quarter at a time until we
      have measured a full-year run.

## Review (2026-09-07)
- Chunk = calendar month (invisible in the output; ReportSort re-sorts every booking).
- WL regression oracle: 16 workbooks old vs new main.py, 0 cell diffs. 3 unit tests (chunking, CSV-safe concat).
- Live pull: Time Advertising (145) 07/01–08/31/2026, two chunks — see e2e note in the commit.
- Zip name `agency_<id>_postlog_<from>-<to>.zip`; download endpoint serves zip or xlsx by extension.

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
   - Lee 9/1: also wanted as a Finish-button combo — Finish a show, then find that show
     on its market log (`_find_traffic_log` + day sheet) and fill its times. Inputs the
     log route already takes: market, date, program window, language. Deferred to Phase 2.
5. Publish stays with the operator for now; auto-publish switch later (Lee 8/28)
- [ ] Design doc `tasks/finish-chain.md` before code: idempotency per step, failure surface,
      what "done" looks like on the Daily Programming page.

## Open items carried
- COMS block target lengths as the evenness source (planner still shortest-first).
- Publish: operator, for now.

---

# Admerasia — Seoul Medical Group (SMG) client (2026-09-04)

Same IO layout as McDonald's; only the CLIENT differs. One order type, one parser, a
client table — do not fork a second OrderType.

- [x] `admerasia_parser.py`: `AdmerasiaClient` profile table (McD 42 (3,5,0) 'McD' / SMG 478
      (15,0,0) 'SMG'), resolved from the `Ref:` line ONLY (notes mention McDonald's on SMG IOs);
      `AdmerasiaOrder.client_name` / `.client`; code/description/notes builders read the profile
- [x] `admerasia_automation.py`: `lookup_customer` keyed on the profile (DB by client name →
      profile fallback); gather prints the real client; self-learning save via CustomerRepository
- [x] `order_detection_service.py`: `_is_admerasia` accepts Admerasia + `Ref:` + `Order Number:`
      (no McDonald's needed); bump `_SCAN_CACHE_VERSION` → 9
- [x] `parser_bridge._normalize_admerasia`: client from the order, not "McDonald's"
- [x] vision prompt text client-neutral; datamover CLIENT_AGENCY gets SMG
- [x] CTV_Customers row for SMG (478, ADMERASIA, SMG, 15/0/0, Seoul Medical Group)
- [x] tests: detection (SMG text w/o McDonald's; Admerasia-only still refused), client
      resolution with McDonald's in the notes, code/desc for both clients, bridge client
- [x] verify: parse both SMG IOs → `Admerasia SMG 1SE 2610` / `Seoul Medical Group Est 1 SEA 2610`
      and `2SE 2610`; McD fixture unchanged; pytest + ruff clean
- [x] memory note + commit + push + post_push.sh

## Review
- Both SMG IOs parse with the existing vision+positional reader (108 spots each, totals foot);
  codes `Admerasia SMG 1SE 2610` / `2SE 2610`, descriptions `Seoul Medical Group Est N SEA 2610`,
  customer 478, separation (15,0,0). Gather dry-run (piped answers) clean; SMG row self-learned
  into CTV_Customers and the second lookup comes from the table.
- 14 new tests (`tests/unit/test_admerasia_clients.py`); 724 unit tests pass; ruff clean.
- Found, not changed: McDonald's CTV_Customers row has `order_type='ADMERASIA'` (uppercase) →
  `OrderType()` rejects it → McD has always used the hardcoded (3,5,0), not the row's 15/0/0.
- `lookup_customer` now returns separation as (customer, ORDER, event) — the order
  `add_contract_line` expects; the old code returned (customer, event, order) from a DB hit.

## Fill & Finish — Maija's 9/7 feedback (2026-09-07)
- [x] moves rolled back ("rebuild left a live NOOP"): conform ORA/ORA_P to the plan in frames before the rebuild; seat in planned order
- [x] planner idempotent: final-break rule measures PI+paid core, not the PSA it placed (LAX MBuhay 9/4)
- [x] FCC ID = the hour's ID on SFO/CVC/DAL (Lee): accepted assets, pre-midnight window plans the FCC asset, generic beside it deleted, FCC moved to the bottom (SFO 9/2 23:30)
- [x] `_seat` opens an XORDER gap (+1000 on later rows) instead of "no XORDER room" (DAL 9/4 01:00)
- [x] page keeps scroll position across a Finish refresh
- [x] dry runs: Boxing Queen NYC 9/3, MBuhay LAX 9/4, Chinese Drama DAL 9/4, Frontline SFO 9/4, SFO 9/2 + 9/3 23:30, DAL 9/5 16:00, NYC 9/4 08:00 oracle — all `finished` on re-plan inside the txn
- [ ] Evening Express 6:00 Sat 9/5 "not placed": not reproducible now (DP had not placed it when Maija looked); ask which market if it recurs
- [ ] FCC sweep after Finish still pushes the fill past midnight until Finish is clicked again — teach `_place_daily_once` to seat behind the hour's fill, or run the sweep before Finish (workflow note)

## Nightly media file-size check (2026-09-08)

Built after the 9/7 freeze (DAL/WDC/NYC on TheOne090726B, 37 MB for 30:41 — truncated at source).
- [x] `business_logic/services/media_integrity.py` — `classify` (truncated < 12,000 B/frame; inconsistent same-codec copies) + `scan` over TPALINSE I/C rows next 2 days, playout devices only (S3 master + CIB1/3/4/5/6; Proxy/WIP/test excluded)
- [x] `scripts/check_media_sizes.py` — CLI, exit 1 on findings; oracle: `today=2026-09-07` flags exactly THEONE090726B
- [x] Broadcast Health: background task (first status poll → scan now, then daily 03:00), `media` in status, `/api/broadcast-health/media`, POST `.../rescan`, page `/master-control/media-check`; header turns amber + toast per finding
- [x] 8 unit tests; in-process smoke (page 200, scan 333 assets)
- [ ] Watch the first live nights for false positives (lowest legit file seen so far: 22,396 B/frame)

## Media check: sibling-relative rule + health-dot event log (2026-09-09, Lee "sure")

Context: TOPNEWS090826A (22,396 B/frame, 58% of siblings) froze 5 markets 9/8; 03:00 scan said 0 findings.

- [x] `media_integrity.py`: `sibling_key(code)` (base = code minus trailing piece letter / rN tag, base must end in a digit); `scan()` loads S3-copy B/frame for the last ~30 days of FILMATI, flags a piece < 75% of the median of its OTHER siblings (kind "short vs siblings"); absolute floor kept as backstop
- [x] Backtest 7/25–9/8 (3,264 assets, module's own classify): flags exactly THEONE072726A (5%), THEONE090726B (2%), TOPNEWS090826A (58%); live scan 9/9–9/11: 374 assets, 0 findings, 3.1 s
- [x] Unit tests for sibling_key + the sibling rule
- [x] `broadcast_health.py`: transition log — diff each new status/media payload against the previous, append JSONL events (offair on/off per station, media finding on/off, feed unknown/back) to `data/broadcast_health_events.jsonl`, bounded
- [x] `GET /api/broadcast-health/events?days=7` + page `/master-control/health-events` (Nord, 🏠 home), linked from media-check page
- [x] Unit tests for the diff → events function
- [x] ruff clean, pytest unit, commit (explicit staging) + push + post_push

Review: kind stays 'truncated' (detail says 'vs siblings'); best copy is compared (a half-copied CIB stays 'inconsistent'); events file data/broadcast_health_events.jsonl per host, bounded 5000→4000 lines; page /master-control/health-events + portal card; 765 unit tests green.

## Ghost-spot check in Broadcast Health (2026-09-09, Lee "go ahead and add the ghost check")

- [x] `business_logic/services/ghost_spots.py`: shared scan (COM rows, LIVELLO 0, no trafficPalinse; dated after today or today+Idle), summarize by creative, format_report; CLI `check_ghost_spots.py` now uses it
- [x] nightly task runs it beside the media scan; status payload `media.ghosts` {count, titles}; header dot amber "N ghost spots", toast; media-check page lists by creative + all rows
- [x] events: ghosts / ghosts_clear transitions, labels on Health Events
- [x] tests: 5 ghost + 1 events; 771 unit green; live CLI + in-process rescan (27 ghosts = the 9/9 rows Lee kept)

## Booked Business: air-date range filter (2026-09-09, Aki via Lee "go ahead and build it, keep the broker fee line")

- [x] `_bb_report_window(year, month, date_from, date_to)` module-level in orders.py: month mode = broadcast/calendar bounds per CENTROMEDIA (unchanged); range mode = one window for every contract, labels say "air dates"
- [x] `/booked-business/load` takes optional `date_from`/`date_to` (ISO); airtime + production queries use the window; WL broker fee line kept in both modes
- [x] template: "Custom dates" toggle beside the month arrows → two date fields (formatDateInput) + Apply; arrows return to month mode; Unset Contracts panel hidden in range mode
- [x] unit test for the window helper; ruff; commit + push + post_push

Review: helper `_bb_report_window` (module-level, tested 7 ways); route parses ISO dates → 400 on bad/missing/reversed; live in-process: Aug 2026 month unchanged ($213,827.86 gross), Aug 12–20 range $67,486.26 gross incl. WL broker fee line; union 7/27–8/31 ≥ month; 778 unit green. Template only (no static cache-bust needed).

## Fill & Finish — Maija's 9/10 feedback (2026-09-10, Lee "really good feedback")
Diagnosis (live DB 9/10): (1)(2) spots after the ID = window membership by clock/anchor;
a spot's block is `trafficPalinse.offset` (EE's block column) and Finish ignored it —
DAL 9/10 17:30 DART15M04 (block 17:30) at 18:00:14 was cut as "next hour's" with no F
anchor at 18:00. (3) DAL 21:30 block emptied = next-block PIs walked into the 20:00–21:30
window as "our fill" and stripped by auto-refill. (4)(5) To the Point / Viet Past&Present =
short shows (9–11 min open) tripped `UNPLACED_SECONDS=300` → "programming not placed".
(6) Korean Drama over = K-FILLER too long; no swap rule.
- [x] `load_day` carries `tp.offset` + COD_PROGRA; `window_from_day` = block membership
      (owned/foreign), unbooked rows keep the XORDER walk; fallback includes owned rows
- [x] apply: seat every planned row in plan order (owned spilled spots ahead of the ID);
      restore SQL backs up ORA/XORDER of every planned row; `_seat` no-op when already seated
- [x] unplaced = no pieces OR catalog letters missing (non-filler bases) OR > 20 min open
- [x] filler swap: overrun + filler piece → longest same-pool filler that fits; swap in-txn
      (mimic DP replace_piece + explode timecodes); page tag 'swap'
- [x] unit tests (membership, catalog, swap pick); ruff; dry runs on DAL 17:30 9/10,
      SEA 14:00 9/10, forced-overrun Korean hour; commit + push + post_push; lessons

Review: 786 unit green (9 new). Live oracles (grid mirrored from K: for WSL): SFO 9/10 22:00,
DAL 9/10 25:30 + 28:00 read "23/9/24 remove" before (next block's PIs) → "finished, 0" after;
SEA 9/11 09:00 Korean Drama dry run: K-FILLER25-015 (12:30) → -039 (10:10), 5 spilled paid
spots seated ahead of the ID, end == plan, rolled back; SEA 9/11 17:00 (10.5 min open, was
"unplaced") dry run lands 12 PIs + ID at 17:59:54. Nothing written to production.
