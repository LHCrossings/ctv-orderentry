# Program Grid in Control Room — Phases 1+2 (2026-08-12)

Spec: `tasks/weekly-schedule-control-room.md` (incl. new Block Identity section).
Scope confirmed by Lee: Phases 1+2 together, one page.

## Phase 1 — read-only grid viewer

- [ ] API `GET /master-control/program-grid/load?station=<cod_user>&week=<monday>` —
      per-day: blocks in offset order (name, ID_TrafficBlock, offset→HH:MM broadcast
      time, duration, PRG/COM segment counts), plus per-day validation verdict
- [ ] Validation per day: start=647352, span=2589408, tiled (no gaps/overlaps beyond
      the known 2-frame Sat/Sun overlap), weekday-pattern-varies check across the week
- [ ] Coverage strip: last programmed date per station (all 10), so the horizon is
      visible at a glance
- [ ] Identity panel: flag ID CHURN — same (station, name, weekday, offset) slot
      using different block IDs across weeks in the horizon (the true picker
      hazard). Stable N-ID sets for multi-airing shows (Shop LC hourly overnights,
      DAL twice-daily shows) are CORRECT, never flagged. Also surface the single
      same-day repeated-block anomaly + WDC's 11-vs-6 Shop LC extras for review
- [ ] Page `templates/master_control/program_grid.html` — week × day grid styled like
      the Etere app (Nord classes, existing patterns: pills for station, date input)
- [ ] Card on Master Control hub linking to it

## Phase 2 — copy week / extend range

- [ ] Rebuild gridcopy logic in repo (module, e.g. `src/web/program_grid.py` or
      alongside orders.py helpers): for each target date, source = same weekday of
      source week; 3 inserts (traffic_schedule, traffic_scheduleblock re-pointing at
      SAME block IDs, Traffic_Calendar Level 0)
- [ ] Guards (all from 8/07, non-negotiable):
      - targets empty (or explicit replace that deletes first, restore .sql written
        to a persistent location first)
      - abort if ANY placed spot in range (TPALINSE LIVELLO=0 + trafficPalinse)
      - per-day both-direction (ID_TrafficBlock, Offset) diff vs source
      - per-day start/span assert
      - weekday-pattern-varies assert (catches "one day copied to all seven")
      - one transaction, verify inside, rollback on mismatch
      - NEVER insert Traffic_Block rows (identity rule #1)
      - never the same block ID twice in one target day (once-per-day rule)
- [ ] API `POST /master-control/program-grid/copy` {stations, source_week,
      target_from, target_to, dry_run=true default}
- [ ] Dry-run returns the planned per-day writes; UI renders preview in the Phase 1
      grid before a separate commit click
- [ ] Audit open question: check `traffic_schedlog` / `Traffic_Writeschedlog` before
      shipping — do Etere edits log there?

## Explicitly OUT of this pass

- Cross-market seeding (NYC → new market, dipendenceid stamping) — Phase 4 territory
- Duplicate cleanup / expiring extra blocks (Phase 3; Shop LC pattern may be
  intentional — ask Lee first)
- Any grid editing (drag/drop, structure changes)

## Review (2026-08-12, commit 95eb1a1)

**Shipped.** Portal card "Programming" → `/programming` hub → "Etere Weekly
Schedules" (`/programming/weekly-schedules`). All checkboxes above done except
noted:

- Router `src/web/routes/programming.py`: stations/coverage, load (week grid +
  per-day validation), identity (ID-churn), copy (dry-run default / commit).
- Viewer shows only scheduled blocks — the ~1,500-per-station expired
  graveyard never appears (Lee's "no expired fluff" ask).
- Identity check reframed per Lee: flags ID CHURN per (weekday, offset) slot,
  never stable multi-ID sets (once-per-day rule). Live findings: NYC Sat 24:00
  runs Shop LC #5289 some weeks / #5341 others (real hazard); DAL shows only
  legit programming changes (The Founders replacing Oriental Wisdom).
- Copy: per-market own-week source, weekday→weekday, same block IDs; guards
  all verified (empty targets refuse tested against live data, >400-day cap,
  placed-spot check, weekday-variance, verify-inside-txn + rollback proven in
  a rolled-back production transaction; SCOPE_IDENTITY batch works under
  pymssql). Undo .sql + audit .json to `logs/program_grid/` before commit.
- Extend mode: blank target-from = each market's horizon + 1 day.
- traffic_schedlog answered: Etere only logs "Put in trash" — copies unlogged.
- Validator tamper-tested: gap/overlap/late-start/short-day/dup-block all
  flagged; 2-frame overlap tolerated. 512 unit tests pass.
- NOT built (deliberate): replace mode (targets must be empty), cross-market
  seeding, duplicate cleanup, editing (Phase 4).
- First real commit click is Lee's (per the offair-utility precedent). Windows
  repo must pull + restart to see the page.
