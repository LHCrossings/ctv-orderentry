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

## Fill & Finish — Ashe's 9/11 notes (2026-09-11, via Lee)
Report: (1) "Internal Server Error" fills the page after a Finish (day list 500); (2) alert
"Unexpected token 'I' … not valid JSON" on DAL Korean Drama (apply 500, write had landed);
(3) alert "Cannot read properties of null (reading 'classList')" when two shows are
finished back to back (schedule fine); (4) ask: multi-select Finish like Daily Programming.
Diagnosis: the Jumpbox server keeps NO log (scheduled task, no redirect), so the two 500s
have no traceback anywhere; list_programs reproduces clean now for LAX/DAL/NYC/MMT 9/10–9/11
(11–21 s from WSL). (3) is a page race: finish A → loadPrograms() wipes the list; finish B
returns while it reloads → getElementById('prg-B') is null.
- [x] finish.py: every endpoint returns JSON `{status:'error', message}` on an exception
      (HTTP 500) and logs the traceback (logger.exception) to `logs/server-errors.log`
- [x] finish.html: read the body as text and only then JSON (real message on a 500);
      coalesce reloads; in-flight shows keep their 'Finishing…' button across a reload;
      re-find elements by identity after a reload; never throw on a missing block
- [x] unit test for the error wrapper; ruff; commit + push + post_push
- [x] (4) multi-select (Lee 9/11: "choose three or four lines, then hit finish"): tick box on
      every finishable show, header button "Finish selected (n)"; one confirm listing the
      shows; applies run ONE AT A TIME in schedule order via the same applyOne() as the
      single button; one re-read at the end; failures named in the header note and left
      ticked. Verified headless (jsdom harness in the session scratchpad): 3 boxes on
      ready/refill/strip shows only, max 1 parallel apply, rollback + 500 both reported.

Review: 26 finish tests green (3 new); node --check on the page script; TestClient smoke:
page 200, simulated DB failure → 500 `{status:'error', message:'OSError: …'}` on /day and
/apply, traceback in logs/server-errors.log, bad market still 400. Root cause of Ashe's two
500s stays UNKNOWN until the next one lands in that log — nothing in Etere was touched.

## Playout binding: one rule, three consumers (2026-09-14)
Renamed programming (Teresa Teng 12/13, Beauty Tycoon 23) arrived code-bound for 9/15-16 even
though DP bound them right at placement: Etere re-ran its support update on exactly those rows
after the rename (Explode signature on the renamed shows only). The rename tool's own rebind
required a CIB copy (`ID_METADEVICE <> 6`) so it was a no-op for anything not yet restored,
and check_bindings had the same blind spot (4 rows reported vs 264).
- [x] `services/playout_binding.py`: FILE_ID = sized, colon-free copy, S3 first; scan / rebind / rebind_asset
- [x] `scripts/check_bindings.py` → CLI over the service
- [x] rename-programming apply → `rebind_asset` (no CIB-copy requirement, I and E rows)
- [x] Broadcast Health nightly: bindings scan (report-only) → header amber, media-check page, event log
- [x] tests: SQL contract, summarize, rename route uses the service, event differ
- [x] lessons.md + memory

## Fill & Finish: DAL 9/12 25:30 "content still sits behind the Station ID" (Ashe 9/14)
- [x] root cause: `_insert_event` matched the new row by (asset, ORA) → soft-deleted twin from the refill strip
- [x] fix: id watermark + LIVELLO=0, raise when no new row; unit tests; live SP repro in a rollback
- [x] Master Control card order (Set up, F&F, Optimize, Logs) — Lee approved 9/17, done

## Media Check: per-file dismiss for master control (2026-09-15, Lee "Let's do the acknowledge control")
Context: McD SEA billboards MD07BBV418 / MD06BBM418 (6-7 s, ~11k B/frame) tripped the 12,000 floor;
files are complete (Lee viewed them). No dismiss existed → dot amber + toast every session through 9/26.
- [x] `services/media_acks.py`: JSON store `data/media_acks.json`, keyed by id_filmati, pinned to the copy size (re-ingest = new alert); `apply()` splits findings into active / dismissed
- [x] `broadcast_health.py`: header summary counts active only; `/api/broadcast-health/media` annotates `ack`; POST `/media/ack`, DELETE `/media/ack/{id}`; event log kinds `media_ack` / `media_unack`
- [x] `media_check.html`: Dismiss button (optional note) on each finding; dismissed cards muted with Undo
- [x] `health_events.html`: labels for the two new kinds; toast hint "dismiss on Media Check"; bump `?v=`
- [x] tests: store + apply (size pin, undo, prune) + event kinds; ruff clean
- [x] commit, push, post_push.sh
- [x] 7SE 2609 phantom OPEN/CLOSE billboard lines 83456/83457: Lee deleted them (planned, not used)

## Playout binding: Daily Programming's own checksum sync rewrote it from the code (2026-09-15, Lee "figure out why this keeps happening")
Root cause: `_apply_filmati_sync` (2ef37da, July triangle fix) writes `supporto = prefix + COD_PROGRA` on EVERY
row of the asset after `_bind_supporto` bound it right; the nightly aligner repairs rows once the file is on a CIB,
so only S3-only assets stay broken (142 rows 9/17; NYC 9/2 black; 9/14 batch). Etere's SP is innocent (rolled-back test).
- [x] `playout_binding.binding(cur, asset_id)` = one function for the value; DP `_bind_supporto`, `_apply_filmati_sync`, Finish `_supporto` use it
- [x] tests: repro-shaped unit test on the sync SQL, consumer grep covers DP + Finish
- [x] lessons.md + memory (actor pinned: ours), commit, push, deploy, re-run check_bindings

## Bee deploy: Tailscale SSH now owns port 22 (2026-09-17, Lee "I got a deploy error from git")
Run 35174061370 (5a73dfa) + its rerun: `ssh: handshake failed: EOF` at appleboy/ssh-action; Connect Tailscale (incl. ping) green.
Banner on 100.105.177.118:22 = `SSH-2.0-Tailscale`; peer record advertises sshHostKeys → Tailscale SSH enabled on the Bee
(host "portal", untagged) some time after the green 9/15 15:33Z deploy. Tailscale SSH ignores DEPLOY_SSH_KEY and needs an
`ssh` ACL rule for tag:ci → Bee as jellee26; none exists, so it drops the handshake. Lee has no Tailscale admin → Kurt/Jenna.
- [ ] Kurt/Jenna: either `sudo tailscale set --ssh=false` on the Bee (OpenSSH + existing key work again), or add ACL ssh rule
      {src tag:ci, dst the Bee, users jellee26, action accept}
- [ ] then `gh run rerun 35174061370 --failed` (or next push) and confirm the Bee is on 5a73dfa
Jumpbox + Windows checkout are on 5a73dfa (post_push verified) — team sees the card order; only the Bee docker lags at 9d871a4.

## EDIT-04 (Jenna's export PC) diagnostics via SSM hybrid node (2026-09-17, Lee + Maija's email)
Registered EDIT-04 as SSM node mi-02599888d685ee9e5 (activation SAC-EXPORT, role SSMHybridServiceRole). Read-only surveys only.
- [x] Exports are complete: 177 uploads since 8/25 size-matched to S3 except the 2 known truncations (TheOne090726B 2%, TopNews090826A 58%);
      local copies are full size, local B has no black frames.
- [x] Root cause of the truncations: Datamover ActiveSync (5-min loop) ingested `\\10.0.0.199\wip\<file>` while the copy from
      Sacramento was still running (dm.job logs: read 12 min / 4 min after render; partial bytes went to S3 and every CIB).
      Tailscale EDIT-04→Datamover is DERP-relayed (no UDP 41641 on the Datamover SG) so copies are slow and stay open long.
- [x] "You Are the One exported in full but black after 30-40%" = the 9/7 hour as aired in NYC/WDC/DAL: A played (28 min), truncated B
      died at ~70 s (TPALINSE DURATION 2099); header said 30:41 so Etere showed it complete. r1 re-export aired clean.
- [ ] Lee: pick the ingest fix — staging folder on the Datamover C: volume + MOVE into WIP (atomic), and/or ask Etere for the
      ActiveSync file-stability setting; optionally allow UDP 41641 from the office IP on sg-002282178e877efcc
- [ ] EDIT-04 hygiene for Jenna/Maija: OS has no cumulative update since 3/2024 (Win10 EOL), L: mapping → 192.168.50.27 is dead
      (343 SMB timeouts/30 d), Edge Default profile still has its 12 bookmarks (nothing wiped locally), power loss 8/20 22:46
- [ ] delete the activation script from the Windows desktop (code single-use, expired 9/18)

## EDIT-04 transport correction + ADSRV01 exposure (2026-09-17, Lee: FileZilla client, not Etere-web FTP)
Corrected the transport (my first write-up wrongly said a direct Tailscale copy to the Datamover):
- Hop 1 EDIT-04 -> ADSRV01 (192.168.50.20) = SMB over Tailscale (EDIT-04 tx 53.9 GB to adsrv01 since 9/14 reboot, <1 MB to Datamover).
- Hop 2 ADSRV01 -> Datamover C:\WIP = FTP: Jumpbox (usrjp, 10.0.0.45) FileZilla client saved site "Crossings" = ADSRV01 Tailscale 100.104.122.111:21 as lee.hudson; downloads land under final name in WIP (Preallocate=0) -> ActiveSync sweeps mid-write = truncation.
- [x] Datamover FileZilla server (port 21/990) = Etere-web only, unused per Lee; ADSRV01 FileZilla server is the real one.
- [ ] SECURITY (new): ADSRV01 FileZilla-Server firewall rule is on the PUBLIC profile; internet host 80.94.95.221 hit :21 with an RDP/mstshash exploit probe. ADSRV01 on office LAN (gw 192.168.50.1) -> office firewall forwards FTP (and likely RDP) from the internet. Scope to Tailscale/office subnet.
- [ ] Fix the ingest race: FTP-pull into C:\WIP_incoming then MOVE into C:\WIP (atomic), or switch that pull to WinSCP (.filepart+rename); optionally ask Etere for an ActiveSync file-stability setting.
- caveat: ADSRV01 FTP logs retain 9/14+ only (9/6-7 gone); 9/14-16 logs show only scanner noise, no internal RETR -> the two original RETR lines unseen; mechanism rests on Datamover dm.job partial-size reads.

## Korean News 9/18 — four markets fail Traffic_InsertEvent (2026-09-18)
- [x] Reproduce: sequential SP insert OK in every market; 4 concurrent → 3× 1205, 1 winner
- [x] Root cause: `_insert_event` swallowed the 1205 raised from `nextset()`; guard fired, tagged non-retryable, no retry / solo pass
- [x] Fix: `_drain_results` re-raises deadlocks; `InsertLeftNoRow` is retryable; all 5 except sites tag `_is_retryable`
- [x] Rejected: Python lock around the SP — hangs (SQL locks of open txns + Python lock cycle), verified rolled back
- [x] Verified: 4-thread rolled-back harness converges on attempt 2; 818 unit tests pass
- [x] Lee reran Set up across markets for 9/18 — "ok, it worked"; all 9 CTV markets hold 5 live parts

## Traffic Instructions agency sheet (Mynt Agency / Pacagen, WorldLink) — drag-drop (2026-09-18)
- [x] Parser `browser_automation/parsers/trafinst_traffic_parser.py`: layout-detected (Code/Name With 800# + % to Run), agency from From:, per-row flights → periods, % reconciliation, duplicate guard
- [x] Route: `_rotation_sheet_item` helper (shared payload builder) + `trafinst` branch/detector/badge; page filter + agency/estimate suffix
- [x] Verified live: parse endpoint → 6/6 creatives found, contract 3081 sole candidate, :30 lines returned; 9 parser tests + full unit suite
- [ ] Maija: next Mynt sheet via drag-drop; 3081 was assigned by hand this time (216 pool rows), no re-assign needed

# ⏳ ACTIVE — IW Group / Covered California IO parser (`iwcca`) (2026-09-23)

Lee: new IO type, client **Covered California (ANAGRAF 386)** via **IW Group (ANAGRAF 12, 15%)**.
Three IOs in `/mnt/c/Work Temp/!New/!Orders/CCA_Crossings TV_0000353{82,93,97}_{Chinese,Vietnamese,Filipino}.pdf`.
Rules from Lee: rates are NET → gross up; all three are CVC (only two say KBTV) → **prompt the market,
default CVC**; AV lines enter as **BNS** (booking 10), never AV; separation **15,0,0**; `OrderNo` →
**Customer Order Ref**; IO `Campaign` + `Description` → contract **notes**; code `IW CCA <yymm> <C|V|T>`;
description `Covered CA Brand Awareness 2610 <Chinese|Viet|Filipino>` (past: `Covered CA VML 2510 Chinese`).

## Format facts (from the PDFs + Etere oracle IW CCA 2510 C/V = contracts 2167/2168)
- Header line: `Station: … Flight Dates: 10/05/2026 - 12/20/2026 (11 weeks) OrderNo: 35382 Date: 9/22/2026`;
  `Campaign:` wraps onto a second line (`… Brannding -` / `Awareness`); `Description:` one line.
- Grid: 12 week columns, day numbers at x = 253 + 24·k (centre match, tol 8); **zero cells are not printed**
  (the flight has 4 dark weeks: 10/26, 11/02, 11/30, 12/21). Each line = data row (`Days Time [DP] Len w… Total Cost Net`)
  + description row below (`AV …` prefix = bonus). Fields by label/position; DP column optional.
- Reconcile and RAISE: per line Σweeks == Total Spots and spots×rate == Net; Subtotal row; **monthly summary**
  (`OCT '26 NOV '26 DEC '26` spots + net, broadcast months) ; `Total Net`.
- Oracle 2167/2168: market 7 (CVC), BNS = booking 10, hand-entered lines were Rotation (PRENOTAZIONE 1), split per
  broadcast month, separation 5/25/0 (Lee now: 15,0,0). 2510 headers carried P_AGENZIA 0 and NET rates — Lee: this time gross up.

## Plan (POP = template, commit 3a6948c; agency header pattern from ntooitive_automation)
- [x] `browser_automation/parsers/iwcca_parser.py`: `IWCCALine`, `IWCCAOrder(rates_are_net=True)`, `is_iwcca_text`, `parse_iwcca(path)`
      (pdfplumber words, cluster rows on raw `top`, week columns from the day-number header, centre-distance cells, reconciliation guards)
- [x] `browser_automation/iwcca_automation.py`: `gather_iwcca_inputs` (customer 386 via customers.db upsert, market prompt [CVC],
      code/description bracket defaults, language letter from the IO language, start-date confirm, separation from DB else 15,0,0),
      `run_iwcca_order` (header: agency fallback `AGENCY_IDS["IW"]=12`, `lookup_customer_defaults=True`, `customer_order_ref=OrderNo`,
      `note=Campaign | Description`, `allow_rename=True`; lines: gross = net/(1−fee) full precision then round(2) like intertrend,
      `consolidate_weeks` per line, `booking_code=10 if bonus else 2`, `separation_intervals=(15,0,0)`, description `"{BNS }M-F 8p-10p Mandarin :30"`)
- [x] `src/domain/enums.py` OrderType.IWCCA; `order_processing_service` (dispatch, `_DIRECT_DB_ORDER_TYPES`, `_process_iwcca_order` returning the gathered code);
      `orchestrator._INPUT_GATHERERS`; `parser_bridge` (`_DISPLAY_NAMES`, `_REGISTRY`, `_normalize_iwcca` with NET per-spot rate + `rates_are_net`,
      `_DIRECT_DB_KEYS`, `_DIRECT_DB_TESTED_KEYS`); `order_detection_service` (text: `TELEVISION ORDER` + `Covered California`; filename `CCA_`), bump `_SCAN_CACHE_VERSION`
- [x] `AGENCY_IDS["IW"] = 12` (ANAGRAF 13 = Lexus Dealer Association, the Lexus docstring is mislabeled) (Lexus docstring says 13 — verify ANAGRAF 13 before touching Lexus; ANAGRAF 386.AGENZIA = 12)
- [x] tests: `tests/fixtures/iwcca/` (3 PDFs) + `tests/unit/test_iwcca_parser.py` (totals per file, dark weeks, AV→bonus, tamper tests: dropped cell / slid cell / blanked rate / renamed header must refuse)
- [x] standalone parse of all 3 (+ full Etere dry run of the Chinese IO: 15 lines, rolled back, nothing left behind) → totals 207/$5,750, 174/$3,360, 80/$1,600; language-window pre-check clean
- [ ] ruff, pytest, commit, push, post_push.sh; memory + lessons

## Decisions (Lee, 9/23): language suffix on the description — yes; Rotation on paid lines like 2510; consolidate equal consecutive weeks (reconciled against the IO's monthly summary).

## McD DC 3097/3098 lines 5-7 reshape to the 9/22 proposal (2026-09-23)

Both contracts identical (Daviselen McD 73/74, WDC, $2,050 each). Nothing placed yet (0 trafficPalinse rows).
Agreed table: L5 2,2,2,1 @ $10 = $70; L6 4,4,4,4 @ $20 = $320; L7 5,4,4,4 @ $20 = $340. Header stays $2,050.

- [x] Read both contracts, confirm 0 placed spots
- [x] Dry run (rollback) of the reshape — scratchpad `mcd_revise.py`
- [x] Lee's go → `--apply` 9/23; restore file logs/mcd-3097-3098-lines567-restore-20260923.sql
- [x] Fresh-connection readback: 88 paid spots / $2,050 per contract, CIG $2,050, both headers $2,050
- [x] Separation 9/23: lines 1-4 + BNS (25,0,0); lines 5-7 (10,25,0) = cust/order/event; restore logs/mcd-3097-3098-separation-restore-20260923.sql

## Production-only EDI invoices on /edi/billing (2026-09-24)
Oracle: Lee's hand-built 2605-011_BVK_PROD.txt (+ PMX 2503-049, !SAMPLE PROD.txt) and the
TVInvoices rendering CRTV-TV202605_2605-011.pdf. Shape: estimate `<est>PRD`, R32 `EST <est>
PRODUCTION`, ONE R51 `Y;<yymm>15;;0611;30;PRODUCTION;<gross cents>`, R33 `PRODUCTION CHARGES`,
R34 gross/commission/net with 1 spot, R12 gross. Trigger case: Crispin 2608-020 ($2,447.06 gross,
$2,080.00 net) alongside airtime 2608-019.
- [x] parse_affidavit: detect "charges are for production only" → is_production; gross from the
      plain `Subtotals 1 $ x` line (no COPY LIST on a production affidavit)
- [x] lookup_contract_customers returns CUSTOMERREF (`Order 212735, Est 0001` → estimate 0001)
- [x] production_invoice(inv, affidavit, estimate) → (inv, [synthetic spot]); constants cite oracle
- [x] _assemble_rows / validate / export: production rows need no CSV; reconcile = production
- [x] billing.html: PRODUCTION badge, excluded from fetch, exportable without CSV
- [x] tests: 31/32/51/33/34/12 records reproduce 2605-011_BVK_PROD byte-for-byte; 2608-020 parse
- [x] live check: _assemble_rows on 2608-019 + 2608-020 → 020 exportable, 019 unchanged
- [x] Lee 9/24 defaults: R51 = 20th of the invoice month at 12:00; estimate verbatim (no PRD);
      R32 left blank for the operator; commission = affidavit gross − net (agency Y / direct N)

## Agency XML Export card (2026-09-25)

Oracle: `/mnt/c/Work Temp/!New/!Orders/BAAQMD_2026_R1C_3131CA-only.xml` (Kurt+Charmaine, ingested by the agency)
built from `Crossings TV Media Proposal_BAAQMD_2026_REV1.xlsm`.

- [x] Read the proposal workbook by header label (Language/Daypart/Discounted Rate/Length/week dates/Total Spots/Proposed Contract Amount); reconcile per line + totals, raise on mismatch
- [x] Emit the AAAA-Message in Kurt's shape (no demos/phones/comments, ProgramName per DayTime, DaypartName = language family, one DetailedPeriod per line, Rate only), validate against the XSD (lxml)
- [x] Regression: BAAQMD workbook + Kurt's header values → canonical-equal to Kurt's XML
- [x] Card on /orders: drop .xlsm → preview + editable header fields → Download XML
- [x] Unit tests (workbook fixture, tampered-cell refusals, oracle equality)
- [x] Update memory; commit + push + deploy (no user correction → no lessons entry)

Review: exporter reproduces Kurt's ingested file tree-for-tree from the REV1 workbook; 900 unit tests
pass; Crispin reader gained a rate×spots == Proposed Contract Amount refusal. Winter 2026 workbook (two tabs) added 9/25: one XML per ticked tab (zip when several), header
memory per advertiser; both Winter XMLs written to Lee's !Orders folder for the joint test.
Lee 9/25: the Discounted Rate Gross column is what the agency wants (120/100 grossed for 15%);
buyer = the sheet's Agency cell (Crispin LLC — BAAQMD's agency changed), memory re-keyed by agency.

## Polaris AAPI TV Schedule reader — Affordable Santa Clara (2026-09-28)
Charmaine's new template (Media Name/Program/Days/Time Period/Daypart/Unit/week/# Spot/GROSS/NET/TOTAL)
- [x] `parse_polaris_xlsx` → dispatcher on header row: old positional reader untouched, new label-mapped reader
- [x] guards: line spots×gross == TOTAL GROSS COST; order == TOTAL GROSS BUDGET == Total row (spots+$); net ratio uniform; >1 week column refuses
- [x] per-line spot length from Unit (`:30`) → `PolarisLine.duration_secs`, automation uses it
- [x] market from the block's `DMA:` cell (SF→SFO, LA→LAX)
- [x] gather defaults (Lee): code `<code_name> <yymmdd start>`, desc `<description_name> <yymmdd>-<yymmdd>`; length check 32/80
- [x] separation tuple order fix (customer, order, event)
- [x] fixtures tests/fixtures/polaris/ (April + Sept); April byte-identical old vs new; tamper tests refuse
- [ ] ruff clean, commit, push, post_push.sh
- [x] Total-row spot guard relaxed to a WARNING (sheet formula `=SUM(J11:J17)` counts 13 of 25; dollars reconcile 3 ways) — surfaced in gather + web bridge header
- [x] Polaris customer lookup/upsert moved from the retired sqlite customers.db to dbo.CTV_Customers (CustomerRepository)

### Review
Reader dispatches on the header row; April fixture parses identical old vs new (10 lines / 26 spots / $4,100). Sept: 7 paid lines, 25 spots, $4,276, SFO, :30. Nine tamper tests refuse; no-op mutation parses equal. 925+ unit tests green. Live entry pending — Lee runs the gather (482, code `Polaris ASC 260929`).

## Polaris naming defaults learned from the contract prompts (2026-09-28)

Lee's first live gather for Affordable Santa Clara (482) saved blank code/description prefixes
(the new-customer prompts had no bracket default) and offered `SFO 260929` + the 130-char sheet
name; he typed `Polaris ASC 260929` / `Affordable Santa Clara 260929-261005` by hand (3132).

- [x] Drop the blind prefix prompts; suggest `Polaris <yymmdd>` / advertiser trimmed to 80 when the record has no prefix
- [x] `_learn_prefixes`: strip the date tails (and market suffix) from what was typed, upsert onto the record with its stored separation
- [x] Fix 482 in dbo.CTV_Customers: `Polaris ASC` / `Affordable Santa Clara` (read back)
- [x] Handler reports the gathered codes (summary printed `Contract POLARIS`)
- [x] Round-trip unit test; Polaris + service suites green (40 passed)
- [x] Header note = the sheet's full committee name (Lee typed it by hand on 3132 after the first save was lost); test pins the `note=` kwarg

## S3 media purge utility — expired program files (2026-09-29)

Lee: Etere's "Delete on -> AWS S3" workflow runs one 3-second Datamover job per file (215 files
= 10 min on 9/27); a batch utility should do the same thing fast. Scope: PROGRAM assets only
(PGM/PGMX); commercials, PSAs, PIs, IDs, bumpers, religious opens stay forever (tiering handles
them); HIATUS-marked assets are never touched. First sweep = assets marked expired (DATA_SCAD
<= today, back to 2021) that still hold an S3 file: 3,856 files / 3.9 TB, mostly Da Ai TV series.
- [x] `services/s3_media_purge.py`: candidate SQL (type + name guards, no future playlist rows), bucket size cross-check, restore SQL (FS_METAFILE/FS_XFILEFILM/FS_FILE/smptemetadata), batched DeleteObjects + one DB txn per batch, fresh-connection verify
- [ ] `scripts/purge_s3_media.py --sweep expired [--apply] [--limit N]` dry-run default; plan CSV to logs/
- [x] unit tests: guards in SQL, literal formatting, only S3-confirmed keys leave the DB, DB failure rolls back, limit, dry run makes no calls
- [x] ruff clean; dry run on live data (3,242 deletable / 3.43 TB; 611 dangling records, 3 size mismatches skipped) — [ ] reviewed by Lee; `--apply --limit 100` first, then the rest
- [x] Control Room page `/traffic/s3-cleanup` (card on /traffic): scan → four categories, one action each (delete / remove dead reference / report-only), show-family groups with checkboxes, confirm(), streamed apply log, rescan. Live scan through the real route: 3,242 / 611 / 3 / 0, 73 groups.
- [x] Invariant "nothing in S3 = expired in Etere" (Lee): every delete / dead-reference removal stamps DATA_SCAD = today in the same transaction; verify checks it; new category "unexpired" (no S3 file, not expired → mark expired). 179 breakers found.
- [x] Date-ranged "old programming" category (Lee: "grab a specific time range … like AVS and NEWSTODAY"): not-expired program files last aired in [from, to] → delete + remove reference + mark expired. Live: to 12/31/2025 = 34,930 files / 24.3 TB in 348 groups; scan 48 s (bucket listing when > 4,000 keys).
- [x] First live delete from the page (Lee, 9/28 21:45 PT): FCI (Fuji programming) 250 records / 311 GB; verified from outside — S3 keys gone, no S3 refs, all expired, zero orphan rows
- [ ] Sort control per category (size default / name / files) — DONE 10acd9d+; keep
- [x] Short-form switch on the scan card (Lee: "turn OFF the commercial exception at certain times"): off by default; on, every category includes COM/PSA/PER/ID/... for that scan and its deletes; HIATUS still guarded; red flag in banner + confirm
- [ ] LATER (Lee): expire + delete the Fujisankei commercials (FUJICM*, ~314 COM files, small) — they will never air again. The page still excludes short-form by rule; needs an explicit per-client commercial path when he asks. Show Maija the page first.

## Daviselen traffic: estimate 1500 assigned to the wrong contract (2026-09-30)

Lee dropped Traffic-16005720..23 (Toyota Oct, estimates 1500-1503). 1501-1503 assigned; 1500
"said assigned" twice and 3077 stayed unassigned. Cause: `LIKE '%1500%'` + `ORDER BY DATA_INIZIO
DESC` + `contracts[0]` picked **IG Pechanga 31500** (3133, starts 10/01) over Daviselen Toyota
1500 (3077, starts 9/28). Both Assign runs wrote the Mandarin Toyota creative 148842 onto all 39
Pechanga Filipino spots 10/01-10/25 and added 148842 to Pechanga's four line pools.

- [x] `estimate_matches` / `pick_contract` in daviselen_traffic_parser.py (token-exact, prefer
      agency 121, never auto-pick when ambiguous) + tests on the real 1500 fixture
- [x] both Daviselen endpoints use it; HL/RPM estimate candidate lists filtered the same way
- [x] card: no contract → list the token matches (or "no contract carries estimate N"), Assign stays off
- [x] repair 3133 (applied 9/30, verified fresh connection; revert logs/pechanga-3133-traffic-repair-restore-20260930.sql): 84746 x9 → 146119 (TM0701, its own line creative), 84747/84748/84749 x30 →
      unassigned (sibling values), delete CONTRATTIFILMATI 633673-633676; dry-run then Lee's go
- [ ] Lee re-drops Traffic-16005723.pdf after deploy → 3077 gets 148842 (14 Oct spots)
- [x] lessons + memory

## H/L Added Value spans the paid days (2026-09-30)

- [x] `paid_span` in added_value.py; both H/L automations feed it the entered paid ranges; tests on 3137-3139 line sets
- [x] live trim of AV lines 84880/84886/84892 (30/34/28 spots), header 3139 → 12/07; ghost check clean; backup logs/hl-toyota-sfo-av-trim-backup-20260930.json

## HPSJ (Health Plan of San Joaquin) house-proposal parser (2026-09-30)

Source: `Crossings TV Proposal_ HPSJ_DSNP_Revised.pdf` — 1-page "Crossings TV Media Proposal",
week columns 10/5..11/30 (10/26 hiatus), 4 paid rows @ $35 (Unit Value $50 gross, Promo Unit
Cost $35 = rate), 3 BONUS ROS rows, Translation Cost row ($600 value / $360 cost), footers
Paid Units 204 / $14,240 / $7,500, Bonus Units 86, Discount 47%. AI fallback misread it.
Oracle: contract 2376 `HPSJ 2601` / `Health Plan of San Joaquin 2601-2603`, ANAGRAF 414 direct
0%, CVC, Calendar billing, lines `M-Sun 11a-12p Viet News/Talk`, `M-Sun 12p-1p Viet Drama`,
`Vietnamese ROS`, WL prio 50. Closest code: sjcounty (xlsx) + scwa (PDF) — neither reads it.

Plan (mirror sjcounty_parser/automation; PDF read by word coordinates, not extract_tables —
the SOUTH ASIAN bonus row comes back with five week cells merged):
- [x] `parsers/hpsj_parser.py`: header fields (Advertiser/Contact/Email/Phone/Market/DATE,
      `Estimate- 10/5 THROUGH 12/7`), week headers by x-centre, rows → HPSJLine (language,
      daypart, length, value, cost=rate, week_spots, units, total_value, total_cost, is_bonus),
      Translation row → charge; reconcile per line (Σweeks==Units, Units×cost==Total Cost),
      per week vs Paid Units / Bonus Units rows, grand totals; RAISE on any mismatch.
      Detection client-keyed: "Health Plan of San Joaquin" (+ filename HPSJ). Fixture + tamper tests.
- [x] `hpsj_automation.py`: gather (customer 414, market CVC, start date via line_planner,
      code/description defaults, separation/billing from customers.db, upsert), create header
      (direct, lookup_customer_defaults, CUSTOMERREF), lines via `_line_plan`/plan_ranges
      (hiatus week splits ranges), bonus = ROS_SCHEDULES per language, translation → Production
      box on first paid line + verify_production_charge, single transaction.
- [x] Register: enums, detection service, order_scanner, orchestrator, order_processing_service,
      parser_bridge (names/registry/direct/tested keys + normalizer), scan cache version bump.
- [x] Decided 9/30 (Lee): union lines for both dual dayparts; `HPSJ 2610` / `... DSNP 2610-2612` / ref DSNP; Calendar; translation → Production box. Open decisions were: Viet Drama "10A-11A & 12P-1P" (two windows), Filipino
      "M-F 4p-7p / Sa-Su 4p-6p" (union M-Su 4p-7p like SJ County?), code/description, Calendar
      billing + separation, DSNP as Customer Order ref.
- [x] Dry run 9/30: 20 lines, 290/290 spots, CENTROMEDIA 317, ref DSNP, production $360 on line 1 — rolled back. NEXT: Lee enters live via drag-drop/CLI.

## Lexus revisions (IW Group / Melissa) — DEFERRED 2026-09-30, flights start 10/9

Two revised Lexus orders received 9/30 for contracts already in Etere. Today's revision path
(`lexus_automation`, filename `REVISED…`/`REVISION…`) only ADDS lines from a cutoff date to an
existing contract; it never diffs against or trims the lines already entered, so a change to
weeks already in Etere would double-book. Lee: revisit; first confirm the revisions carry the
corrections the Melissa Check flagged on her last set (do not build a revision on a wrong IO).
- [x] 9/30 R3 vs R4 cell diff (both estimates): Jan-Apr, Jun-Sep and the 9/28-9/30 week are identical;
      only MAY differs — same per-line May totals and dollars, re-spread across weeks (R4 puts spots in
      4/27-4/30 and 5/8-5/10, drops the 6/wk and 3/wk weeks). Etere 2579/2581 carry R3's May spread + June MG
      lines; May has aired, so no entry impact. Melissa Check: 0 warnings on both R4 sheets.
- [x] DONE 9/30: Lee deleted the 10/1+ lines in the Etere app, ran the R4 sheets with cutoff 10/1 →
      A on 2742/2747. Readback: every Oct-Dec line/week matches R4 (248 + 288 spots), all placed, 0 ghosts.
- [ ] (was) either (1) hand-trim superseded lines + run the additive path, or (2) build a diff-based
      Lexus revision (parse revised sheet → diff vs CONTRATTIRIGHE → unschedule superseded future
      weeks in both tables → add changed lines), reusing the WL revision engine where possible

## Gauger + Associates parser — Shea Homes IO 90658 (2026-10-01)

Agency "Broadcast Order" PDF (one page, NET rates). Oracle contract 823 `Gauger Shea 2304` (Apr 2023):
net $102 → gross $120, $85 → $100 at Gauger's 15% (ANAGRAF 262; Shea Homes 263 linked), SFO, 15/0/0.
Lee's choices (10/1): code `Gauger Shea 2610`, description `Shea Homes Opal-Emerald 2610`, customer ref
`Order 90658`; one Etere line per IO row over the whole flight (Rotation, no week columns); the bonus
row keeps its table window (`BNS M-Su 1p-4p Hindi/Punjabi`).
- [x] `browser_automation/parsers/gauger_parser.py` — header fields by label, table by header x
      positions, program names completed from the Desc block, reconcile line totals / paid spots /
      TOTAL SPOTS / NET and raise
- [x] `browser_automation/gauger_automation.py` — gather (customer 263 default, market from the IO,
      code/desc/ref defaults above, gross-up preview), direct-DB entry with `--dry-run` rollback
- [x] register: enums, detection (text "Broadcast Order" + "Gauger + Associates"), orchestrator,
      processing service (+ direct-DB set), parser_bridge (display, registry, direct keys, NET normalizer),
      AGENCY_IDS, scan cache v13
- [x] tests: real IO fixture `tests/fixtures/gauger/`, tampering guards, normalizer
- [x] dry run against the live DB, rolled back (10/1: 4 lines, gross $5,780.00, ref 'Order 90658', commission 15%); readback of IMPORTO / CUSTOMERREF / N_PASSAGGI

## Illinois Lottery / Flowers Communications parser — FY'27 MCM Media Plan (2026-10-01)

House "Crossings TV Proposal" PDF (one landscape page, 20 week columns 10/12/26-2/22/27, GROSS
discounted rates, :15). Oracles: contracts 1541 `Flowers ILLot 2410` and 2151 `Flowers ILLot 2510`
(customer 354 Illinois Lottery via agency 353 Flowers @15%, CMP, Broadcast 316, 15/0/0, every line
Rotation, consolidated week ranges). Lee's choices (10/1): line names = days + time + sheet row
with house short block names (Cantonese & Mandarin → Chinese), bonus `BNS <lang> ROS` on the house
ROS windows; Rotation on every line; code `Flowers ILLot 2610`, description `Illinois Lottery
2610-2702`, customer ref blank.
- [x] `browser_automation/parsers/illottery_parser.py` — header by label, week columns by header
      x (year from "Flight Estimate … 2027", Monday/ascending check), wrapped row names attach
      downward, Discounted Rate = the rate, reconcile Units / Proposed per line and Total / Gross /
      Net (= gross × 0.85) and raise
- [x] `browser_automation/illottery_automation.py` — gather (always-ask start, customer 354,
      CMP), consolidate_weeks + plan_ranges, Rotation, `--dry-run`
- [x] register everywhere (enums, detection before the Charmaine fallback, orchestrator,
      processing service, bridge normalizer GROSS, scan cache v14)
- [x] tests on the real fixture + tampering; dry run rolled back (10/1: 46 lines, 1356 spots, $17,644.00, 15%)

## Order Lookup card for the AEs (Charmaine) — /reports/order-lookup (2026-10-02)

Lee: AEs email "is this order in? does it have spots assigned, which ones?" (Admerasia McD Monopoly
10/1). One search box that covers code, description, customer ref, notes, client, agency and line
descriptions (the campaign name "Monopoly" lives ONLY in the header notes; the Vietnamese cut is
the "VT" suffix of the Admerasia estimate in CUSTOMERREF). After the search, state the facts in
plain English, then list the creatives.
- [x] `src/business_logic/services/order_lookup.py` — pure `summarize(hdr, lines, creatives, today)`
      → sentences (entered / scheduled N of M / creatives assigned / aired so far)
- [x] `reports.py`: `/reports/order-lookup` page, `/api/reports/order-lookup/search?q=` (tokenised
      AND match over the haystack, TOP 50, per-hit counts), `/api/reports/order-lookup/{id}` detail
- [x] `templates/reports/order_lookup.html` modelled on placement_by_week.html
- [x] card on `reports.html`
- [x] unit test for the summary sentences on the 3131 shape + partial/unassigned/finished shapes
- [x] verify live against 3129-3131 (all scheduled, creatives assigned), 2146 (finished flight, 23 aired),
      3133 (16 spots still need a creative) — plus language search via CTV_LineLanguage ("monopoly vietnamese")

## Separation columns: literal everywhere (2026-10-06)

Lee: "we need to get that cleaned up and correct" — the (customer, event, order) tuple convention
is a relic of the old Etere web form that had Order/Event swapped (since fixed); we bypass the web
form now, so columns must mean what they say.

- [x] Canonical tuple = (customer, order, event) — what `add_contract_line(separation_intervals=)`,
      the orchestrator prompt and data-reference already say. `Customer.get_separation_intervals()` too.
- [x] Every automation reads `(sc, cust.separation_order, cust.separation_event)` and writes
      `separation_order=sep[1], separation_event=sep[2]`; constants' comments say (customer, order, event).
- [x] Structural test: no `separation_event=…[1]`, no `separation_event, …separation_order)` reader left.
- [x] CTV_Customers data: rows with event≠order written by a swapped writer get the two columns swapped,
      using the customer's own Etere lines (INTERVALLO=order, INTERV_CONTRATTO=event) as the oracle.
- [x] Lessons + data-reference + memory updated.

## CAFOB parser (2026-10-06)

- [x] Parser `cafob_parser.py` (table by header label, year inference, 3-way reconcile) + fixture + 12 tests
- [x] Automation `cafob_automation.py` (house rules: consolidate, :30, sep cap 25, Priority, Matson 483)
- [x] 7 registration points + scan cache v15; dry run 7 lines / 41 spots rolled back; deployed aa80302
- [x] 3155 header: P_AGENZIA 15 (via Lee's code edit), COD_USER 7 → 1 by UPDATE 10/6

## San Mateo County Voters parser (2026-10-07)

Lee: direct client of Charmaine's, already in Etere (ANAGRAF 196 "County of San Mateo", direct 0%,
Broadcast 316, SFO, sep 25/0/0); order carries an Editing (production) cost; it is LATE (10/5 flight,
entered 10/7+) so the start date is always asked and the short weeks get their own caps.
Oracle: contract 2700 `San Mateo 2605` / `San Mateo County Voters 2605-2606` (May 2026, same layout).

- [x] `parsers/sanmateo_parser.py` — "Media Campaign" house layout by word coordinates (Insertion /
      Time / Cost / week cols / Units / Airtime total), wrapped insertion+time rows, `ROS Bonus` rows,
      footer Paid / Bonuses / Airtime cost / <charge rows> / Total Amount; 3-way reconcile, raise.
      Year from the filename or the Monday rule (no DATE on the sheet).
- [x] `line_planner.plan_ranges`: a truncated LAST week (flight ends mid-week: 11/2 is one Monday
      holding 2 spots) gets its own cap/line, same rule as the short first week.
- [x] `sanmateo_automation.py` — gather (market [SFO], start date always asked, plan preview,
      customer [196], code `San Mateo <yymm>`, desc `San Mateo County Voters <yymm>-<yymm>`, ref = title),
      direct-DB entry with Editing → Production box on the first paid line (verified), `--dry-run`.
- [x] 7 registration points (enum, detection text+filename, orchestrator, processing service, bridge
      names/registry/direct/tested, scan cache v16).
- [x] Fixture `tests/fixtures/sanmateo/` + tests (shape, totals, charge, plan for a 10/8 start, tampering).
- [x] Dry run against Etere (rolled back), ruff, full unit suite, commit, push (hook deploys).

Review: dry run (10/8 start) → 24 lines, 120/120 spots, Production $375 on line 1 (CONTRATTISPESE verified),
CENTROMEDIA 316, agency 0%, rolled back. 31 new tests; crispin/ntooitive/hpsj planner tests unchanged.
Open: Lee to confirm code `San Mateo 2610` / desc `San Mateo County Voters 2610-2611` at the gather prompt.

## SacRT 2608 (2945) revision — 10/10 start + production charge (2026-10-09)

- [x] Read both PDFs (airtime 8/3-10/30 $4,988 NET, 12 lines; PROD $1,220 NET) and contract 2945 (0 placed, no blacklist, no charges)
- [x] `scripts/repair_sacrt_2945_revision.py` — dry run (rollback) then `--apply`, modeled on the Shea 3150 start-move script
- [x] Lines: DATA_INIZIO + DATESTART -> 10/10, end 10/30 kept; caps M-F paid 1->2/day, Hmong Sa-Su 1->3/day, bonus 1/day
- [x] ContrattiImportiGiornalieri re-spread 10/10-10/30 (81 rows, $4,988.00)
- [x] CONTRATTISPESE 'Production' $1,220 on line 80697 dated 10/10, PRINTDETAIL=1 (shape of charges 188-203)
- [x] Header 10/10-10/30, LISTINO/LISTINOORIGINALE/SCONTATO 4,988 -> 6,208; verified from a fresh connection
- Restore: `logs/sacrt-2945-revision-restore-20261009.sql`

### Review
Nothing was placed, so no unschedule or ghost check was needed. The charge date follows the SP convention (first paid line's flight start) rather than the 20th; both land in October for the Calendar header.
- [x] Separation (Lee 10/9, same session): paid 60 -> 25/0/0, Hmong paid + all six bonus lines -> 15/0/0; `scripts/repair_sacrt_2945_separation.py`, restore `logs/sacrt-2945-separation-restore-20261009.sql`; break-capacity test showed Hmong Sa-Su 6-8p needs all 4 breaks that 25 min allows
- [x] :15 conversion (Lee 10/9, same session; client delivered :15s, plan footnote converts each :30 to two :15s): `scripts/repair_sacrt_2945_to_15s.py` — DURATA 450, 158 -> 316 spots, paid $43 -> $21.50, caps x2 (Hmong BNS 1 -> 3), separation 15/0/0 everywhere, Hmong 5/0/0; airtime $4,988 / header $6,208 / day revenue unchanged; Lee unscheduled in the app first; ghost check clean; restore `logs/sacrt-2945-to15s-restore-20261009.sql`

## Lexus 26Q4 weekend pull + make-good, blacklist utility (2026-10-09)

- [x] Melissa (IW Group): remove Fri 10/9 - Sun 10/11 on IW Lexus 202 NYC 2740 / 208 SFO 2742 / 210 SFO 2747, make good 10/12-10/18 (28 spots, Idle, no copy)
- [x] Lee pulled the weekend with /scripts/delete-spots -> 28 ghost playlist rows (trafficPalinse-only delete); check_ghost_spots said clean (predicate needed 'COM' + a creative). Ghosts deleted with backup `logs/lexus-2610-ghosts-restore-20261009.sql`, verified 0 from a fresh connection
- [x] `scripts/repair_lexus_2610_weekend_makegood.py` --apply: 9 whole-weekend lines MOVED to 10/12-10/18 ("MG " prefix), 12 spanning lines TRIMMED (start 10/12, N_PASSAGGI - removed) + 12 "MG " copies added via add_contract_line (85676-85687), day revenue rebuilt; money/headers unchanged; restore `logs/lexus-2610-weekend-makegood-restore-20261009.sql`
- [x] GHOST_WHERE widened to COM/COMS/BNS/AV regardless of ID_FILMATI (test updated)
- [x] New Control Room card /scripts/blacklist-spots (`scripts/blacklist_scheduled_spots.py`): same inputs as delete-spots, removes both tables + Traffic_ScheduleList insert/PassageMiss, refuses aired spots, backup .sql, in-txn verify, accounting readback; rehearsed with rollback on 2945 10/10
- [ ] delete-spots still deletes trafficPalinse only (Lee: keep for now) — flagged
- Note: Lee renamed contract 2945's code to `SacRT 2610`

## Vida Agency / Puget Sound Energy "Flex" parser + late-start entry (2026-10-12)

Source: `/mnt/c/Work Temp/!New/!Orders/Crossings TV Media Proposal_ The Vida Agency_Puget Sound Flex _Revised.pdf`
(Charmaine "Crossings TV Media Proposal" template, 2 pages, SEA, :30, NET rates). AE note: Tuesday 10/13 start,
NO agency commission, creative PUGET30V03 "Puget Flex 1008 VIE" (FILMATI 151183), enter the whole order, December
traffic unknown → note to check in late Nov.

Facts gathered:
- ANAGRAF: The Vida Agency = 260 (Commissione 15, FlagAgent 2); Puget Sound Energy = 344 (AGENZIA 260, Commissione 0).
  Precedent contracts 1476 `Vida PSE 2410` / 1589 `Vida PSE 2411`: P_AGENZIA 0.00, CENTROMEDIA 317 Calendar,
  desc `Puget Sound Energy - <campaign> <yymm>`, lines `M-Su 11a-12n Viet` / `M-Su 12n-1p Viet` / `BONUS M-Su Viet ROS`,
  SEA (5), Rotation, separation 5/0/0, rate = net.
- Order: p1 "October & December" (weeks 12-Oct..2-Nov = flight 10/12-11/8; 30-Nov..28-Dec = flight 12/1-12/31),
  p2 "March 2027" (1-Mar..29-Mar, flight 3/1-3/31). Lines: Viet News/Variety M-Su 11a-12p $40; Viet Drama M-Su
  10a-11a/12p-1p $40 (dual daypart); Bonus Viet ROS $0 (value $40). Production p1: Value $1,950, Net Amt $1,000;
  Net amount $14,000 = airtime 13,000 + 1,000. p2 airtime 7,000. Page-2 row names WRAP onto their own line; money
  prints with stray spaces (`$ 4 0.00`, `$ 1 ,000.00`).
- `create_contract_header(agency_pct=0.0)` is honoured over the ANAGRAF lookup (etere_direct_client.py:672);
  `media_center_id` kwarg exists. Vietnamese CTV window (language_windows.py:58) = M-Su 10:00-13:00.

Plan:
- [ ] `browser_automation/parsers/vida_parser.py`: `parse_vida(path)` → `VidaOrder` (per-page tables → flights by
      header date ranges; week columns by header label; wrapped row names; `_money()` tolerant of spaces, None on
      unreadable; `rates_are_net=True`). Reconcile and RAISE: rate×units == Value per line; Σ week cells == Total
      Units; PAID/BONUSES rows per column; Σ Net Amt (+ production net) == Net amount. Flight = contract unit.
- [ ] `browser_automation/vida_automation.py` (clone of ntooitive_automation): gather (customer 344 / agency 260,
      commission 0 explicit, billing from ANAGRAF/precedent Calendar, separation default 5/0/0 from precedent,
      code/desc previews per flight, start date ALWAYS asked — default Tue 10/13 for flight 1, 12/1 for flight 2,
      3/1 for flight 3), `line_planner.plan_ranges` per flight (short first week own cap, short last week),
      production on the first paid line of contract 1 + `verify_production_charge`, CTV_Customers upsert,
      December note on contract 2 (line note ≤399 chars: "check traffic/creative for December").
- [ ] Registration (7 files): OrderType.VIDA; service handler + `_DIRECT_DB_ORDER_TYPES`; bridge `_REGISTRY` /
      `_DIRECT_DB_KEYS` / `_DIRECT_DB_TESTED_KEYS`; orchestrator `_INPUT_GATHERERS`; detection
      ("Crossings TV Media Proposal" + "The Vida Agency", BEFORE the Charmaine fallback) + filename rule +
      `_SCAN_CACHE_VERSION` bump; charmaine exclusions; `AGENCY_IDS["VIDA"]=260`.
- [ ] Tests: fixture `tests/fixtures/vida/` (this PDF); pin 3 flights / 3 lines each / unit totals 325+108, 175+64;
      tamper tests (dropped cell, blanked rate, slid column) refuse; line-plan tests for Tue 10/13, Tue 12/1, short
      last week 3/29-3/31; structural test that the handler imports the bridge's parser.
- [ ] Live entry (Lee's go-ahead given: "put the entire order"): run the gather, show the per-flight plan, enter 3
      contracts, read back lines/production from a fresh connection, run language-window check.
- [ ] Memory: project_vida_parser.md; reminder for December creative.

### Review (2026-10-12, entered)
- [x] Parser + automation + 7-file registration + 20 tests (fixture `tests/fixtures/vida/`); full unit suite 1,162 green
- [x] Rehearsed with rollback (3 contracts), then LIVE: **3172 Vida PSE 2610** (9 lines, 195 spots, $6,000 + Production $1,000 on line 85788), **3173 Vida PSE 2612** (7 lines, 238, $7,000, note on lines), **3174 Vida PSE 2703** (8 lines, 239, $7,000, note on lines). Customer 344 / agency 260 / P_AGENZIA 0 / SEA / separation 5-0-0 / ROWSTATUS 0 / blocks on every line; 0 placed at readback
- [x] Mishap: entry ran with CENTROMEDIA 316 after a failed Calendar patch; hand-set to 317, Lee: "I want broadcast" → back to 316 (verified). Charmaine confirmed Broadcast 10/12 — CLOSED
- [ ] Traffic: PUGET30V03 "Puget Flex 1008 VIE" (151183) for 2610 once scheduled; December/March creative unknown — line notes on 3173/3174 say so

## Backwrite monthly breakdown chronological across a year boundary (2026-10-12)
- [x] SC breakdown keyed months by NAME, sorted by calendar position → January above December on 3173 (Lee)
- [x] Sheet1 pivot sorted "Month Year" strings alphabetically (August above July)
- [x] Both key on the broadcast-month date now; labels derived at write time; `tests/unit/test_backwrite_monthly_order.py`
- [x] Verified on the live 3173 workbook: SC December, January; pivot December 2026, January 2027

## Bookend pairs: per-language scope (2026-10-12)

Lee: Imprenta PGE TS 26Q4 (3167) has Cantonese + Mandarin bookend lines and a Chinese
ROS bonus line; the bookend utility assigned one pair set contract-wide.

- [x] `_bookend_breaks(rows, language)` stamps each break with the language airing at its
      (weekday, ORA) via `classify_language_frames`; filter keeps one language
- [x] load endpoint returns `languages` (break/spot counts + lines); page gets a Language select
- [x] assign endpoint takes `language`; PERCROTATION only on the scoped lines; 0% pool copies
      dropped contract-wide
- [x] tests/unit/test_bookend_pairs_language.py; live check 3167: Cantonese 21 / Mandarin 39, 0 bad
- [ ] Lee assigns the two pairs on /traffic/bookend-pairs (Cantonese, then Mandarin)

## MediaLive bitrate drop, every output -1 Mbps (2026-10-12, Lee "see if anyone notices")
- [x] NYC, LAX: 6→5 Mbps, GOP 15→30, refs 3, B-ref on, look-ahead high (Main profile kept)
- [x] SFO 8→7 + Tower 4→3, CVC/CMP/SEA/HOU/MMT/WDC 8→7, all with refs 3 / B-ref / LA high, GOP 90 kept
- [x] Each channel stopped ~55 s, RUNNING 20-30 s after start; readback exact, no other encoder drift; restore files logs/medialive-*-20261012.json
- [ ] Verify the egress drop on the October bill (USW2-DataTransfer-Out-Bytes, Sep = 53,136 GB / $4,531; expect ~5.3 TB/mo less ≈ $430/mo)
- [ ] Watch for distributor/viewer complaints for two weeks; Charter NYC/LAX and Comcast x2 pulls

## Finish/DP deadlock storm — Maija "wait time longer", Ashe 3×1205 on SEA 10/11 (2026-10-13)

- [x] Lee granted VIEW SERVER STATE to etere_app; read 16,286 system_health deadlock graphs since 7/10
- [x] Root cause: `_insert_event` watermark SELECTs seek TPALINSE#ID_FILMATI2 across sibling markets' uncommitted rows (98% of all 1205s); Saturday 06:26 PT SEA 10/11 Finish (Bee) vs Jumpbox DP 10/13-10/14 E/C = 7 victims in 70 s
- [x] Fix: both watermark reads `WITH (NOLOCK)` + structural test; `scripts/deadlock_report.py`
- [x] 1173 unit tests green; ruff clean
- [ ] Verify on the next real DP runs: `uv run python3 scripts/deadlock_report.py --since 2026-10-14` should show near-zero graphs per run
- [ ] Reply to Maija (draft in the session summary)
- [ ] If residual Finish 1205s persist after the fix, lengthen `apply_window_retrying` delays (2/4/8 s) — not before

## Snoqualmie Casino parser — Entertainment :15 proposal, late start (2026-10-15)

Lee: new parser for the house "2026 Crossings TV Media Proposal" (Snoqualmie Casino & Hotel,
SEA, 10/12-11/11, PROMO rates, two Subtitle charges → ONE production amount); late order →
"what date should this order start" + line_planner; check whether week 1 can fit before
planning make-goods.

- [x] `browser_automation/parsers/snoqualmie_parser.py` — text reader (Vida family): header
      labels, week columns by label, rows `<name> $NET $PROMO cells total $VALUE $NET`,
      Paid/Bonus/Total footer, Subtitle charge rows, Final cost, traffic notes; reconcile and raise
- [x] `browser_automation/snoqualmie_automation.py` — direct customer 417, 0%, no agency,
      Broadcast; start date always asked; plan_ranges; production = Σ subtitle net on the first
      paid line; traffic notes → line note; code `Snoqualmie <yymm>` (precedent 2422)
- [x] Registration: enums, detection (+filename), orchestrator, processing service (+direct-DB),
      parser_bridge (display, registry, 2 key lists), scan cache v18
- [x] Tests + fixture `tests/fixtures/snoqualmie/`; ruff; full unit suite
- [x] Fit check: SEA break capacity + separation vs the week-1 plan for a 10/15 / 10/16 start
- [x] Rehearsed with rollback: 40 lines, production $570 on the first paid line, CENTROMEDIA 316, ref 'Entertainment 2026' — nothing persisted (fresh-connection check)
- [x] LIVE entry (Lee 10/15): **3177 `Snoqualmie Casino 2610-2611`**, 40 lines, 318 spots, Production $570 on 85897, CENTROMEDIA 316, catalog V/K/T/M/MC complete; "Viet" alias added to guess_language (b9c75ec3)
- [ ] Make-goods for week 1 if Lee starts 10/15: fit check says ~10 spots short at 15-min separation (Korean 8-10a over-subscribed, Viet 11a-1p breaks already full); Monday 10/19 fits everything but drops week 1 (53 paid + 28 bonus)

### Review
- Parser: text reader (Vida family), 23 tests, fixture `tests/fixtures/snoqualmie/`; PROMO rate entered, NET column = value rate; Final cost reconciled both ways; charge rows kept, combined at entry
- Automation: direct 417, agency none, 0%, Broadcast/Calendar prompted from the sheet's Billing Cycle, start date always asked, line_planner splits the short first week (4/day Chinese 7p-9p, 5/day Filipino 4p-7p on Thu+Fri), traffic notes → line note
- Full unit suite 1,203 green; ruff clean


## opAD NYSDOH 3180 — gather ran clean, entry crashed (2026-10-15)
- [x] Handler passed `user_input=` to `process_opad_order(pre_gathered_inputs=)`; Intertrend had the same drift → both fixed, pinned by `tests/unit/test_handler_kwargs_match_automations.py` (signature sweep over every `_process_*_order` call)
- [x] Parser: client regex stops at `Estimate:` (was "NYS Department of Health Estimate: 3180"); day pattern by shape — the 12/14 rows say "M-Tu", parsed as "M"; fixture + 6 tests, foots 175 / $5,625
- [x] Gather defaults from the customer's prior opAD contracts: customer 44, code `opAD NYDOH 3180`, description `NYS Dept of Health Est 3180` (Lee typed `… OE 4Q26 Est 3180`; the PDF Description line prints above the prompt); typed values saved to customers.db
- [x] Lee entered 3178 `opAD NYDOH 3180` 10/15; NOTE = the IO Description line (writer fixed a84183fa)

## Phoenix TV SRT recorder on VIDEO-02 (2026-10-14)

Goal: record Phoenix TV's SRT feed directly with ffmpeg on the Sacramento office PC VIDEO-02,
replacing the Datavideo -> SDI -> DeckLink -> Etere Memory chain on EDIT-02 (which stays as backup).

Facts (Datavideo Stats page 10/14): Datavideo is the CALLER to `srt://47.181.223.221:6005?mode=caller`,
no stream id; 1920x1080i 29.97 H.264 + AAC stereo, ~6 Mbps, RTT 28 ms, 0% loss. Native house format,
so `-c copy` needs no conversion. ffmpeg can be a second caller in parallel.

VIDEO-02: Win10 Ent 19045, 2x Xeon E5620, 16 GB, one WD 500 GB HDD (325 GB free), power plan never
sleeps, NICs AT&T 192.168.50.111 (default gw) + Comcast 10.1.50.26, Tailscale 100.90.230.98.
SSM hybrid node `mi-02adb6308ecd7a523` (activation sacramento-office-video02-phoenix-recorder, 10/14).

- [x] Enroll VIDEO-02 in SSM
- [x] Read-only inventory
- [x] Install ffmpeg 9.0.2 (nssm.cc was 503 → Scheduled Task instead); Defender exclusion on rec
- [x] Probe: REFUSED while the Datavideo held the slot (same public IP, no passphrase, both ISPs);
      connected first try once Lee pointed the Datavideo at NYC → Phoenix's listener is SINGLE-CALLER
- [x] Recorder live 00:22 PT: hourly clock-aligned .ts → .mp4 remux (first remux OK 00:36), stall guard,
      status.json; Scheduled Task PhoenixSrtRecorder (SYSTEM, at startup, restart 999x)
- [x] Retention 72 h (Lee: 5 days → 3 days), floor 80 GB free
- [x] UDP relay to the Datavideo (10.1.50.98:6005) so Memory records in parallel for now (Lee 10/15):
      any ffmpeg remux froze/blacked the Pi decoder → v2 = byte-exact relay ffmpeg + recorder ffmpeg on
      loopback 6010 (02:04 PT); picture confirmed 02:08
- [x] Memory export vs ours compared (Memory = re-encode at 6 Mbps, one AAC; ours = original + 2 audio)
- [x] Memory note + lesson
- [x] Datavideo input = udp://@:6005
- [ ] Verify 01:00 PT hour rollover; reboot test of the startup task; compare a Memory file vs ours
      (Lee copies one to C:\PhoenixSRT\compare); AT&T-vs-Comcast egress preference

## Finish: ask for a filler when the open time exceeds what PIs can cover (2026-10-19)
Maija 10/19: Korean Drama Saturday (3 episodes, 28 min open) and If You're Hired (54 min in 90) refuse with
"more than fill can cover". Lee: ask for fillers, default least recently aired (changeable), always after the show.
- [x] plan_window: state `needs_filler` (catalog complete, remainder > 20 min) + `filler_options` (language pool, fits, last aired per market, LRA first)
- [x] plan_window/apply_window: `fillers=[fid]` insert each filler as a PGM piece after the show (behind the final break's spots), then the normal fill
- [x] routes/finish.py: `filler` (csv) + `language` on plan, `fillers` + `language` on apply
- [x] finish.html: header dropdown (LRA default) + "Add filler + Finish"; plan body offers a second filler when one is not enough
- [x] tests: option ranking, synthetic piece, state machine, route body, template pin (1239 unit tests green)
- [x] rehearsal: NYC 10/17 08:00 with the hand PIs soft-deleted in the same transaction -> 28:18 open, 57 options, K-FILLER25-009 (LRA, 6/11) -> ready 16:43, 18 PIs + ID, rollback verified from a fresh connection

Review: the 20-minute backstop stays; it now routes to `needs_filler` when the catalog is complete and the language has a pool
(Korean, Chinese/Mandarin/Cantonese, Filipino). Vietnamese has VIETFILLER assets but no pool in filler_rotation: a Vietnamese
show over 20 min still reads "no filler pool for Vietnamese" (follow-up if it ever happens). Rotation = last aired in the same
market's playlist, not DP's kfiller_rotation cycle table.

## Viewpoint Ads / Mayor Lurie Proposition parser (2026-10-21)

Lee: political SF order via Viewpoint Ads (Charmaine), client name TBD — Etere customer 487
"Mayor Lurie Proposition" (agency 486 Viewpoint Ads, 15%, Broadcast) created ahead of time.
Two proposals (Option 1 $30,340 / 169 spots, Option 2 $21,266 / 115 spots), flight 10/23
through 11/3 "ends at 7am", no week columns, GROSS rates, net = ×0.85. Prime the parser so
Maija can enter whichever option arrives while Lee is asleep.

- [x] `browser_automation/parsers/viewpoint_parser.py` — text-level reader, days-token row
      regex, wrapped program words, truncated "Entertainm", market from the station cell,
      year from PDF metadata, cutoff-aware line ends, reconciliation (row rate×units, Σ units /
      gross / net vs TOTAL row and the budget lines, consistent net ratio) → refuse
- [x] `browser_automation/viewpoint_automation.py` — gather (market, ANAGRAF 487 + agency 486
      check, billing, start date always asked, code/desc defaults by Polaris political shape,
      learned prefixes → customers.db), plan (Rotation, max/day from active days in the window),
      direct-DB entry with header readback (P_AGENZIA == sheet %, AGENZIA, CENTROMEDIA), --dry-run
- [x] registrations: enums, detection (agency text + VIEWPOINT filename, before the Charmaine
      fallback), orchestrator, processing service (+ direct keys), parser_bridge (display,
      registry, direct + tested keys), scan cache v19
- [x] fixtures `tests/fixtures/viewpoint/` + `tests/unit/test_viewpoint_parser.py`
- [x] dry run both options against Etere (rolled back), ruff, full unit suite, commit, push, deploy
- [x] memory + lessons

Review (2026-10-21): both options parse and foot (Option 1: 10 lines / 169 / $30,340.00 / $25,789.00;
Option 2: 10 lines / 115 / $21,266.00 / $18,076.10). Dry runs against Etere in a rolled-back
transaction wrote 10 Rotation lines each with AGENZIA 486 @ 15.00 and CENTROMEDIA 316; the
election-day cutoff keeps only the two 6a-7a lines on 11/3. Detection returns VIEWPOINT from
text and filename, the web preview carries the lines with real money, the orchestrator's
language-window check finds no issues, 1323 unit tests pass. Open for Lee: the default code /
description shape (`Viewpoint Lurie 261023` / `Mayor Lurie Proposition 261023-261103`) and the
separation default (house 15/0/0 vs the sheet's "no separation requirement" vs Polaris's 25).

## Campaign PSA fill rule (2026-10-21)

Lee: three :30 PSAs (English, Chinese simplified, Vietnamese), 10/30/2026-10/30/2027, all markets
incl. DAL, 5-10x/day in any programming. Decision: FILL path (Finish / Daily Programming priority
filler), not an order. Cap 8 per market-day, one per show, prefer ~2 h apart, LRA per market.
Chinese → Mandarin shows, Vietnamese → Vietnamese shows, English elsewhere.

- [x] map where Finish and Daily Programming choose PI/PSA fillers (one function or two)
- [x] campaign table (asset ids, language, flight) + selection rule ahead of the normal rotation
- [x] cap / spacing / one-per-show / LRA; DAL language windows; inactive outside the flight
- [x] tests (planner on a real day in a rolled-back txn; mirror test if two pickers)
- [x] aired-count report `scripts/campaign_psa_report.py`; the Finish plan lists the row as a PSA insert with the campaign note
- [x] asset names from Maija (10/22): nine cuts ICE{60,102,81}E / ICE{60,125,130}M / ICE{60,105,81}V,
      1:00-2:10 each, FILMATI 152006-152014; Cantonese shows → the Chinese "M" cut (Lee 10/22)
- [x] planner takes the least recently aired cut of the language that FITS the open time

Review (2026-10-21): general PI/PSA fill is Finish's `plan()` only (Daily Programming fills the
Marketplace / weekend drama with specific assets, untouched). `campaign_psa.py` holds the campaign
(flight, cap 8, gap 2 h, creatives by COD_PROGRA per language — EMPTY until Maija's files land) and
the rule; `plan()` gets a CAMPAIGN phase ahead of the PI fill (shortest interior break) and treats
the campaign rows as the last fill to remove; `plan_window` passes the pick and the reason note.
Read-only rehearsal with stand-in PIs on SFO/DAL 10/20: fresh windows seat it, finished hours stay
finished (idempotent), the language falls back to the day-aware windows when the grid word is empty
(19:30 Cantonese → English, DAL 05:30 → English). Unit: 19 new tests, suite 1,347.

## California Black Media (CBM) parser — SOS Vote Education Campaign 3 (2026-10-26)

Lee: new parser for California Black Media; precedent contract 2203 `CBM 2511` (customer ANAGRAF 404
direct 0%, Broadcast 316, LAX/SFO/CVC, 15/0/0, Rotation). Two documents arrive together: the CBM
Advertising Insertion Order (`SOS_C3_IO_Crossings_TV.docx.pdf`, Docusign) and Charmaine's house
"Crossings TV Media Proposal" (`Crossings TV ACoM_ SOS 2026.pdf`, Agency "AMERICAN COMMUNITY
MEDIA: SOS", Dana Levine). The IO carries the campaign name, flight, per-market paid+bonus counts
and the $2,000 total but NO dayparts or rates; the proposal carries the lines (3 market grids with
their own Paid/Bonus footers + a Summary of investment). Both detect UNKNOWN today.

- [x] `browser_automation/parsers/cbm_parser.py` — `parse_cbm(path)` accepts EITHER file (Lee: proposal is the order, IO optional) and finds
      the companion in the same folder (IO: "CBM Representative"/"1809 S Street"/"Insertion Order:";
      proposal: "Crossings TV Media Proposal" + "Summary of investment" + same markets/total).
      IO reader: date, Insertion Order name, flight per market row, `N (P+B)` counts, TOTAL, length
      (`40 × :30`), language. Proposal reader: extract_tables (header, 3 market grids, summary);
      money cells with split digits (`$ 7 14.00`); week headers `26-Oct` with year inferred
      (Monday nearest the IO flight start). Reconcile: per line weeks = Total Units and rate×units
      = NET; per grid Paid/Bonus footers; summary row per market; IO per-market `(P+B)` and TOTAL
      vs proposal → refuse on any mismatch.
- [x] `browser_automation/cbm_automation.py` — gather (customer 404 default via prompt_customer_id,
      ANAGRAF readback, markets from the proposal confirmed, start date always asked,
      code/description defaults per Lee's pick, CUSTOMERREF = IO campaign name, separation
      15/0/0 from CTV_Customers or default), one contract, lines per market section via
      consolidate_weeks + plan_ranges, BNS lines on the language ROS window, Production none,
      `--dry-run` rolls back with header + line readback; both files move to Entered/.
- [x] registrations: enums, detection (IO text + proposal-with-companion; filename `SOS_`/`CBM`),
      orchestrator, processing service (+ direct keys), parser_bridge (display, registry, direct +
      tested keys), scan cache v21, CTV_Customers row for 404
- [x] fixtures `tests/fixtures/cbm/` (both PDFs) + `tests/unit/test_cbm_parser.py` (header, lines,
      companion lookup both directions, tampering guards, automation defaults/plan)
- [x] dry run against Etere (rolled back), ruff, unit suite, commit, push, deploy
- [x] memory + lessons

Review (2026-10-26, fea795ae): Lee's picks at the prompt — proposal only (IO optional, reconciled when
present), `CBM 2611` / `CA Black Media - SOS Vote Education C3 2611` (fitted: the header SP keeps 50 of
DESCRIZIONE's 80 chars, the first dry run wrote 51 and read back 50), ACM-2881 line names with the bonus
on the South Asian 1p-4p window, 15/0/0. Dry runs through both files (rolled back): 15 lines / 40 spots /
$2,000, header 316 / agency 0 / 0% / NYC / INVOICEMODE 0, week 2 on Monday 11/2 at 2/day. Detection
returns CBM for both texts and filenames; preview carries 9 lines with NET money; language-window
check clean; 21 tests + suite 1384; deployed, Jumpbox UP. First live entry pending Lee.

## ACM October services money — contract 3211 (2026-10-26)

Lee pasted ACM's email ($1,560 subtitling + proofing, "add to October billing", no airtime, agency
and client = American Community Media at 0%). Entered as six hidden line listings (2203's
production-line shape; reference only, CBM is not a party), Calendar 317 dated 10/26 so it books
as October (Lee's call), after a rolled-back dry run and his go-ahead.

- [x] `add_contract_line(hide_from_scheduler=)` — the SP flag was hardcoded off
- [x] `scripts/enter_acm_services_2610.py` — dry run default, readback (header, lines, hide, 0 placed, CIG) before commit
- [x] live: 3211 `ACM 2610`, fresh-connection readback 6 lines / $1,560 / hide 1 / 0 placed / CIG 10/26 $1,560
- [x] commit, push, deploy; memory `project_services_only_contracts.md`
- [x] 10/26 follow-up: Lee renamed the code `ACM PROD 2610`; lines moved NYC → LAX ("California client"), blocks reloaded, 0 placed, $1,560 intact

## Daviselen So Cal Toyota → Rotation (2026-10-27)

Lee: 1x/week M-F / M-Su program lines under Priority always land Monday; make every
Daviselen line Rotation, and flip the four December estimates awaiting approval.

- [x] `daviselen_automation.py`: `DAVISELEN_SCHEDULING_TYPE = 1` passed on the one
      `add_contract_line` site (only path; the module is direct-DB end to end)
- [x] `tests/unit/test_daviselen_scheduling_type.py` pins the call site + constant (AST)
- [x] `scripts/repair_daviselen_3213_rotation.py` (dry run / --apply, refuses placed lines,
      fresh-connection readback): 3213-3216 — 25 lines, 19 paid flipped 0→1, 6 bonus already 1,
      0 placed. Readback: 25/25 Rotation.

### Review
Priority first-fit was Etere's default for a narrow program window (`_default_prenotazione`);
the client's auto-detect only picks Rotation for monthly or >2h windows. Same shape as the
WorldLink 9/7 change. Charmaine (the other Daviselen-system agency) untouched.

## Aircheck "records instantly" on an already-aired WDC spot (2026-10-27)
- [x] `_fetch_etere_spots`: upcoming filter + first-airing pick in Python, in PT (SQL compared Eastern air time to the Pacific clock)
- [x] Air Checks page Schedule All: skip captures already on the agent, never post a past start
- [x] tests: rendered SQL has no GETDATE-vs-ORA compare; WDC 21:08 ET at 19:15 PT is NOT upcoming
- [x] deploy; book 3189 MCIM137526VH next airing (10/29 21:18 ET); remove the 3 duplicate 10/28 captures
- [x] lessons.md + memory

## Portal header button (Kurt, 2026-10-28)
- [x] static/js/portal-link.js injected site-wide (app.py middleware, last </body>)
- [x] clones the page's 🏠 as a second button → http://portal.home/; home button untouched
- [x] tests/unit/test_portal_link_injection.py; verified on the Jumpbox served page

## IW CCA OE Action 35569/35581/35588 would not parse (2026-10-28)
- [x] header per labelled field (Market: added, Flight Dates moved to line 2)
- [x] grid read across every page; continuation header rows dropped; columns asserted
- [x] three IOs as fixtures + tests; six older fixtures unchanged; deployed f5870a0

## ACM parser accepts Charmaine's ACoM proposal PDF — Asian Law Caucus, held pending approval (2026-10-28)
Lee: hold the order; it is listed as American Community Media (if it turns out to be CA Black Media,
Charmaine edits the PDF so the CBM parser runs). Split lines so 11/2 runs its full window and 11/3 only
to 5p. Separation 3/0/0 for this order. Production $3,000 → Production box.
- [x] cbm_parser: generic ACoM proposal reader (`parse_acom_proposal`): any `Agency: AMERICAN COMMUNITY MEDIA: <client>`, date-RANGE week column (`10/30-11/3`), Production rows, Paid-only footer, summary by language + Production + Total Contract
- [x] acm_parser: `parse_acm(path)` dispatcher (xlsx → parse_acm_xlsx, pdf → parse_acm_pdf) → AcmOrder (+ client_name, production, no_separation, billing_cycle, week_end)
- [x] acm_automation: PDF gather (start/end confirm, cutoff prompt, 3/0/0 when the sheet says no separation, `ACM <yymm>` / `ACM <client> <yymm>` defaults with precedent); entry: per-range caps, cutoff split (full window through the day before, last day to the cutoff), production box + verify
- [x] detection: ACoM non-SOS PDF → ACM (CBM keeps SOS); scan cache v22; service + bridge call `parse_acm`
- [x] fixture tests/fixtures/acm/ + tests (parse, detection both ways, cutoff split, tamper); full unit suite; deploy
- [x] dry run on the real PDF through gather + `--dry-run` entry; lesson + memory

## Ingest-stall watchdog in Broadcast Health (Lee, 2026-10-29)
Datamover (EtereMM9) crashes in the background; jobs for it sit in wf_job with no error anywhere
(10/11, 10/12, 10/21 Korean News, 10/23, 10/28). Lee: alert via Control Room so MC restarts it.
- [x] `business_logic/services/ingest_watchdog.py`: wf_job = open steps; ingest (class 12) non-Wait step > 20 min, restore (18-21/30) Datamover step > 45 min = stall; start_cnt >= 5 = looping, counted, never alarms. Thresholds from 5 days of wf_log (ingest steps < 1 min except the 10/28 crash; 06:00 restore burst queues up to 13 min)
- [x] `scripts/check_ingest_stalls.py` (CLI, `--all`); live run: 6 open steps, all loopers, 0 stalls
- [x] broadcast_health.py: 5-min loop, `ingest` in /status, `/api/broadcast-health/ingest` + `/rescan`; snapshot + `ingest_stall`/`ingest_clear` events
- [x] header dot amber "Datamover stalled · N files", toast with the restart runbook, Media Check top section + "Check Datamover now", Health Events labels; js `?v=20261029`
- [x] tests (10 watchdog + 1 events), full unit suite, TestClient smoke incl. simulated stall → events
### Review
Runbook text uses Lee's words (close Datamover, Etere Ingest, Etere Active Sync; sign out, sign in).
Not done: cleaning the 6 looping instances (Lee's call), Etere ticket for a v36 MM9 build.

## Abort the looping workflow instances (Lee, 2026-10-29)
- [x] census: the 346 "Delete on CIBn" instances with start_cnt >= 5 are the DESIGN (Wait [1d] → check schedule → wait again; hundreds complete daily) — untouched. True loopers = 6 instances with an open non-Wait step the Agent aborts/restarts every 20 h
- [x] `scripts/abort_looping_workflow_instances.py` (list / --rehearse / --apply): Etere's own `wf_runtime_instanceAbort`, backup JSON in logs/, in-txn verify (state 8, 0 jobs), fresh-connection readback. Aborted instances stay aborted (737 in state 8 for 660 days)
- [x] applied: 1116885, 915478, 1012932, 1012933, 1116887, 1529711 → state 8; watchdog: 0 open steps, 0 looping
### Review
MCIM032324VH still has two CIB3 copies ("Too many metafile selected") — the file stays on CIB3; HMONG-REPORT031625F/G and V-TOPNEWS071725C assets have no file anywhere and are expired.

## Phantom colon-named file records (Lee, 2026-10-29)
- [x] `scripts/delete_phantom_file_records.py` (list / --rehearse / --apply): FS_XFILEFILM + smptemetadata, then `fs_metafile_delete`; verify real copies, FS_METAFILE -N, no new workflow instance
- [x] Lee rehearsed + applied from the Jumpbox: 45 removed, readback 0 orphan links / file rows; 6 "DO NOT USE" PI-LF records on CIB5 (no real file) left by design
### Review
Phantoms dated Jan-Feb 2025 (ids), re-stamped by Break Optimization's PI swap via the FILMATI trigger; nothing creates new ones.

## Inventory Report (Maija → Charmaine; Lee, 2026-10-29)
Avails: for a date range (+ optional broadcast time range), markets and languages as bubbles, show per
program block how much COMS break time is still unsold and how many breaks still have room (5 spots
into 4 breaks will not place). Maija's sheet shape (LANGUAGE | PROGRAM DAY/TIME | TITLE | BREAKS) plus
the open time; day-by-day detail under each row; Excel export + print-to-PDF.
- [x] `business_logic/services/inventory_report.py`: grid COMS segments per market/date (Traffic_Calendar
      Level 0 → scheduleblock → block → segment visible COMS), sold = trafficPalinse-backed TPALINSE rows
      (COM/COMS/BNS/AV, LIVELLO 0) attributed to the segment at tp.offset (fallback: nearest segment of the
      block containing ORA); pure `build()` + `scan(conn, …)` + `build_excel()`
- [x] route `/reports/inventory`, `/api/reports/inventory`, `/api/reports/inventory/excel`; hub card
- [x] template with date/time fields, market + language bubbles (multi-select), summary table per market
      grouped by language (day pattern, time, title, breaks, airings, open s, open/airing, breaks with room),
      expandable daily cap/sold/open per date; print CSS for PDF
- [x] tests on `build()` (attribution, open-break threshold 15 s, language/time filters, post-midnight, day pattern)
- [x] live run 11/2-11/8: matches Maija's BREAKS column row for row (To the Point 4, Punjab Update 3, …); 10 markets in 3.6 s; deployed
### Review
Grid names each weekday's drama by title, so "Filipino Drama M-F 5p-6p" shows as per-title rows with day letters. Open = COMS seconds not taken by COM/COMS/BNS/AV; room = ≥15 s. Ordered-but-unplaced spots are not subtracted (future).
