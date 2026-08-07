# Program Grid in Control Room (replace Etere "Weekly Schedule")

**Status:** spec / not started. Written 2026-08-07 straight after rebuilding WDC + MMT
by hand, so the mechanics below are all verified against production.

**Goal.** Build and extend the program grid from Control Room instead of Etere's
Weekly Schedule app. The grid is the foundation everything else sits on — no spot can
be placed until the day's PRG/COM structure exists — and Etere's own tool is slow and,
as of 2026-08-07, provably able to refuse a valid copy for reasons that exist nowhere
in the data.

---

## Why (the 2026-08-07 incident)

Maija tried to copy all markets out to 6/27/27. Eight markets went fine. WDC and MMT
refused with:

```
Unable to continue, there are some days with a duration greater than 24 hours
12/28/2026 … 1/3/2027
```

The claim is false, and the same 7 dates are named **regardless of which source week
or which destination week** is chosen. Everything checkable was checked:

| Checked | Result |
|---|---|
| Day span from `traffic_block.Duration` + offsets | exactly 24:00:00 (06:00→30:00) every day |
| Etere's own `trf_getDayStructureList()` | same — every day ends at frame 3236760 |
| `Traffic_DayStructure` view | same |
| Block layout (gaps / overlaps) | WDC's week is byte-identical to CVC's, which copies fine |
| Duplicate calendar rows / shared schedules / cross-station blocks | none |
| `Trf_instanceblock` / `trf_instancesegment` per-date overrides | empty in range |
| `traffic_segment.hourprev` / `durprev` cache | all NULL in every market |
| `Users.RolloverFrame` per-station broadcast-day start | 647352 (06:00) in all 10 markets |

**Nothing in the database distinguishes a station that copies from one that doesn't.**
The validation is in the Etere Air Sales desktop binary. Reproducing the copy in SQL
took ~1s per market and verified clean; Etere never succeeded at all.

A red herring worth remembering: WDC had 20 orphan `traffic_schedule` rows named for
those exact 7 dates whose block totals summed to 30–67 hours — a seductive match to the
error. Clearing them changed nothing. They were residue from the failed attempts, not
the cause. (Still worth cleaning; see Phase 3.)

---

## The data model (confirmed)

One day of grid, per station:

```
Traffic_Calendar   (Date, Cod_User, Level=0, ID_TrafficSchedule, Notes,
                    JingleInserted, blockautoinsert, blockautoinsertUser)
  -> traffic_schedule      (Name, Flag, Notes, Cod_User, Insert_Date, Expired)
    -> traffic_scheduleblock (ID_TrafficSchedule, ID_TrafficBlock, Locked, Offset)
      -> traffic_block       (Name, Duration, Cod_User, Types, Expired, …)
        -> traffic_segment   (Offset, Duration, MaxDuration, Type PRGS|COMS, visible, …)
```

* **`traffic_block` / `traffic_segment` are shared station assets** — a copy never
  duplicates them, it re-points at the same `ID_TrafficBlock`. Segments are where the
  PRG/COM breaks live (`Type='PRGS'` / `'COMS'`).
* **`Traffic_DayStructure`, `…Ex`, `…Ex_Calc`, `…WithOutSplit`, `…WithSplit` are all
  VIEWS.** No cache to invalidate after a write — verify with `sys.objects.type_desc`
  before trusting this again after an Etere upgrade.
* **`trf_priceblock` is empty database-wide** — not part of the copy. (It *is* part of
  block auto-load for contract lines; see `.claude/documents/data-reference.md`. If it
  ever gets populated, a grid copy must extend it too or blocks stop offering.)
* Schedule `Name` is a meaningless inherited label — live CVC days are labelled
  `Schedule of 11/29/2021 usrdvr`. **Never key anything on it.**
* All three PKs are IDENTITY.

### Broadcast-day invariants

Frame-of-day at 29.97fps, broadcast day **06:00 → 30:00**:

* day start `647352`, day end `3236760`, span `2589408` (= 24h)
* post-midnight is 24:00–29:59 on the *same* `DATA` (see `tasks/lessons.md`)
* Sat/Sun sum to `2589410` — a **2-frame overlap** present in every market; normal,
  not a defect. Any check must tolerate it.

---

## What "copy a week" actually does

Derived by diffing CVC 12/28/26 against CVC 1/4/27 (0 differing rows), then used to
write 357 days:

For each target date, source = same weekday of the source week:
1. `INSERT traffic_schedule` copying the source row's Name/Flag/Notes/Cod_User/
   Insert_Date/Expired → new identity id
2. `INSERT traffic_scheduleblock SELECT new_id, ID_TrafficBlock, Locked, Offset`
   from the source schedule (same blocks, same offsets)
3. `INSERT Traffic_Calendar` at Level 0 for the new date, copying the source calendar
   row's Notes/JingleInserted/blockautoinsert/blockautoinsertUser

Working implementation: `gridcopy.py` from the 2026-08-07 session (scratchpad —
**port it into the repo as Phase 2**).

---

## Phases

**Phase 1 — read-only grid viewer.** Render a week × station grid from the tables
above, styled like the Etere app so Maija can cross-check. Show per-day block count,
span, and PRG/COM segment counts. Flag any day that isn't exactly 24h, has a gap, or
has an overlap beyond the known 2 frames. Pure read — immediately useful as the
verification surface for later phases.

**Phase 2 — copy week / extend range.** Productize `gridcopy.py`: pick station(s),
source week, target range; dry-run by default; commit only after per-day verification.
This is the piece that unblocks Maija and it already works.

Guards (all were used on 2026-08-07 — keep every one):
* target dates must be empty, or an explicit *replace* that deletes first
* **abort if any placed spot exists in range** — `TPALINSE` (LIVELLO=0) and
  `trafficPalinse`; deleting a grid under placed spots is how ghost spots are born
  (see the ghost-spot lesson)
* per day, diff `(ID_TrafficBlock, Offset)` against the source **both directions**
* per day, assert start=647352 and span=2589408
* assert the weekday pattern varies (Mon-Fri vs Sat/Sun block counts) — this is what
  catches the "one day copied to all seven" failure Etere produced in WDC
* one transaction, verify before commit, roll back on any mismatch
* write a restore `.sql` of anything deleted, somewhere persistent (not scratchpad)

**Phase 3 — housekeeping.** Report and clear orphan `traffic_schedule` rows (no
`Traffic_Calendar` row) whose named dates fall in an upcoming window. WDC had 20; every
market has hundreds of harmless historical ones, so scope by date window and never
delete blindly.

**Phase 4 — editing.** Drag/drop a program block into a day, change a day's structure,
insert/remove blocks. Needs offset recalculation to keep the day tiled to exactly 24h,
which is where Etere's own checks earn their keep — do not start this until Phase 1's
validator is trusted.

---

## Open questions for implementation

* **Audit.** `traffic_schedlog` exists and Etere has `Traffic_Writeschedlog`. Does
  Etere log schedule edits there, and should we write matching rows? Check before
  Phase 2 ships, so our changes aren't invisible to Etere's own history.
* **`Traffic_Trash`** carries `ID_TrafficSchedule` — is a deleted schedule supposed to
  land there rather than being hard-deleted?
* **`Level`** is always 0 in current data. Confirm nothing uses non-zero before
  assuming it.
* **Etere upgrades** may add columns to these tables — re-dump
  `schema/etere/tables.txt` and diff after any upgrade (see the schema-snapshot memory).
* Does Etere's own copy set `Locked`, or is it always 0 from source?

---

## Also worth doing

Report the false ">24 hours" error to Etere with the evidence table above. It is
reproducible, station-specific, and independent of both source and destination week —
they will want the 12/28/2026 boundary case.
