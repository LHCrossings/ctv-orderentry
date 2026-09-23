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
