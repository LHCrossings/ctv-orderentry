# DAL Kids FCC ID → daily end-of-day placement (2026-07-14)

**Decision (Lee, group decision recalled 2026-07-14):** All programming carries an
E/I logo, so the only FCC requirement is notifying the public where children's-
programming records reside. One ID per day suffices — not one per children's show.

**New rules:**
- SFO/CVC: unchanged (ID 2891, F-anchor, top of PRGS break 1, per Children show)
- DAL: ID 83128 places **every day**, in the **last COMS break before 24:00**,
  EVENT_TYPE 'T', as close to midnight as possible. No longer tied to Children shows.
- Trigger: piggyback on Daily Programming setup runs that include DAL (idempotent)
- Horizon: each run sweeps today → today+7 (plus the run's own date if beyond that)

**Ground truth checked (live DB):** DAL always has a clean last-COMS-before-24:00
target (7/12: 23:24:00, 7/13: 23:58:29); post-midnight programming starts at 24:00:00.

## Plan

- [x] 1. `show_profiles.py`: remove the DAL element from the Children seed profile;
      add a "DAL FCC ID (daily)" seed profile with `daily: true` config
      (elements: id 83128, markets [DAL], placement last-COMS-before-24:00, type T)
      and a `daily_elements()` helper. `profile_for()` ignores it (no code_re/label).
- [x] 2. `daily_programming_run.py`: new `sweep_daily_ids(conn, cod_user, dates, pending)`
      (+ `_place_daily_once`) — per date: skip if no published COMS segments before
      24:00; skip if 83128 already live (LIVELLO=0, ORA<24h) that date; else insert
      into last pre-midnight COMS seg (`_insert_event` + `sch_UpdateSupportAndProperties`
      + EVENT_TYPE 'T'), rebuild + checksum-sync + commit. Deadlock retry w/ jitter.
      Best-effort last-in-break; BO / master control finalizes.
- [x] 3. Run route (`orders.py`): after the solo second pass, every market in the run
      with daily elements gets a sweep of today→+7 (plus the run's own date) and one
      aggregated result row ("End-of-day FCC ID · placed 7/16, 7/17 · …").
- [x] 4. Seed script needed NO change — it already inserts defaults missing by name
      (and by design never updates existing rows). Re-ran it: daily row seeded id=3.
- [x] 5. Live `chat.show_profiles` updated: Children row config 2→1 elements
      (DAL element removed); daily profile row inserted (id 3, sort 30, enabled).
- [x] 6. Verified live (2026-07-14): sweep placed 83128 on 7/16–7/22, each in that
      day's last pre-midnight COMS break (7/16 @ 23:58:29, type T, NEWTYPE ID, dur
      750f); rerun = "already placed" no-op; unpublished day → "not published yet"
      skip; Children @DAL now yields no elements while SFO/CVC still get IDKIDS15E04;
      daily profile never leaks through profile_for.

## Review

- Placement style: insert at break-start ORA as type T — floats, so break
  optimization / master control settles it; MC manually settles it as the LAST
  item aired in the calendar day (Lee 2026-07-14). MC was already hand-placing
  this (7/14 had one at 23:59:44 before the sweep ran) — automation now matches
  the manual practice.
- Transition artifact: days already set up under the old per-show rule (7/15 had
  IDs at 16:28 + 16:58 from Children setups) are treated as covered — the
  idempotency check is "83128 live anywhere before midnight". Compliant (an ID
  airs), just not end-of-day; washes out as those days air.
- Windows server must pull + restart for the route hook to go live; until then
  the 7/16–7/22 placements I made directly keep DAL covered.

---

# Backwrite Pipeline — NEXT: multi-contract fix (NOT done)

Spec: tasks/backwrite-pipeline.md — see the "⚠ KNOWN GAP" bullet in §3.

Phases 0–4 work for SINGLE-contract orders. **Multi-contract PDFs are broken:**
a Toyota HL PDF made 3 contracts (2949/2950/2951, one manifest); the awaiting
flow backwrote only contract[0], then archived the whole manifest — the other
two were dropped. (Found 2026-07-13; Lee out of time that night.)

Three coupled causes (all in the awaiting flow, orders.py + app.js):
- [ ] `list_awaiting_backwrite` (~orders.py:1866) returns one row per manifest FILE,
      not per contract → expand to one row per (manifest, contract_index), labeled by code
- [ ] `doBackwrite` + contact modal (app.js) always POST with no contract_index (defaults
      to 0) → each row must carry + send its own contract_index (GET /contact already takes it)
- [ ] POST archives the whole manifest on first success → archive to Used/ ONLY when every
      contract is backwritten. Track completed indexes (e.g. write `backwritten:[...]` into
      the manifest); archive when the set is complete.

Verify: enter/refetch a 3-contract manifest → 3 awaiting rows → backwrite each →
manifest stays until the 3rd, then archives. Single-contract path must still work.

Then Phase 5 (legacy cutover) after a full clean billing cycle.

---

# ALSO UNFIXED: Pending tab badge/hang (found 2026-07-13)

Pending badge shows 0 on-tab but flips to 15 when you click Awaiting; clicking
Pending then hangs. NOT a backwrite bug — the order queue.

- Badge (`/api/orders/counts` → `_count_files`) = raw file count in incoming = 15.
- List (`/api/orders` → `_scan_dir`/`scan_for_orders`) = detected orders only = 0.
  So 15 files in incoming/ aren't recognized as orders.
- `refreshCounts()` skips the active tab's badge, so Pending's badge only updates
  to 15 once you leave Pending → the confusing 0→15 flip.
- Hang: clicking Pending runs order detection (maybe OCR) on all 15 unrecognized
  files with no spinner/timeout.

Fix directions: (1) list should show undetectable files as "Unknown" rows (badge
then matches, user can delete cruft); (2) per-file detection timeout + loading
spinner; (3) inspect/clear the 15 stray files in the Jumpbox's incoming/.
Full detail: memory `project_pending_queue_badge_hang.md`.
