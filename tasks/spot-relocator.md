# Spot Relocator — Design & Plan

**Goal:** A utility that places spots Etere's first-come-first-served scheduler left
unplaced, by **creatively shuffling moveable spots** (same day first, then other days)
to open a separation-valid slot — proving a feasible shuffle in memory *before* writing
anything. Replaces the manual SE trial-and-error loop.

Status: **discovery / design** (no code yet). Pilot contract below.

---

## Manual workflow today (SE) — what we're automating

1. Find a pod with a *blocker* spot occupying a slot the unplaced spot needs.
2. Blacklist the blocker in SE — **this keeps its traffic/creative attached**.
3. Insert the unplaced spot into the freed slot.
4. Use SE's function to reschedule the blacklisted (removed) spot into any booked break
   **on that date**; if it won't place, open another date and rerun; repeat until it lands.

It's trial-and-error against the live system — you don't know up front if a feasible
shuffle exists. That's the search problem the tool solves.

---

## Constraint / domain model (established with Lee)

- **Separation is advertiser-level**, across *all* of that advertiser's contracts — NOT
  per-contract, NOT all spots in the break. Pilot: 4Imprint = `Interv_Committente` 20 min.
  Two spots of the same advertiser may not sit in pods whose air times are < sep apart.
- **Any commercial order can go in any COMS break.** Block/price-list eligibility is
  irrelevant — scrap that idea.
- The real bind: pods that are sep-valid for the stuck spot are **full** (capacity), often
  with wide-window fillers (e.g. 2-min Direct Donor spots). Pods with open space are too
  close to the advertiser's existing spots (sep violation).
- **Moveable scope (for now): WL spots only** (`COD_CONTRATTO LIKE 'WL%'`). Don't move
  paying Crossings clients.
- **Traffic preservation:** if a moved spot has creative attached (`TPALINSE.ID_FILMATI > 0`),
  the creative MUST stay attached on re-placement. `ID_FILMATI <= 0` (e.g. -1) = none, no concern.
- **Re-place same day first**, fall to other days in the spot's flight only if needed.

## Data model (verified)

- **Placed spot:** `trafficPalinse` (`ID_ContrattiRighe` → line, `id_tpalinse` → TPALINSE,
  active when `ID_TRAFFICTRASH` IS NULL or 0) → `TPALINSE`:
  - `DATA_PREV` = air date; `ORA` = air **time in frames** (÷29.97 = seconds); `ORA_PREV` unused (0)
  - `DURATION` = length in frames; `ID_FILMATI` = creative (≤0 means none)
  - `LIVELLO` = 0 active; `COD_USER` = market (1=NYC); `TYPE` ('T' = spot)
- **Line flight/constraints:** `CONTRATTIRIGHE` — `DATA_INIZIO/DATA_FINE`, day flags
  `LUNEDI..DOMENICA`, window `ORA_INIZIO/ORA_FINE` (frames), `N_PASSAGGI` (ordered),
  `PASSAGGI_GIORNALIERI` (max/day), separation `Interv_Committente / INTERVALLO / INTERV_CONTRATTO`
  (frames → min), `COD_USER` (market). Shortfall = `N_PASSAGGI − placed`.
- **Advertiser:** `CONTRATTITESTATA.COMMITTENTE` → `ANAGRAF.RAG_SOCIAL`.
- **Blacklist:** `Traffic_ScheduleList` (`BlackList>0`, `PassageMiss`). Existing tools:
  `/scripts/release-blacklist`, `/scripts/max-spots`, `/orders/make-goods`, break-optimization
  (all in `src/web/routes/orders.py`).

## Architecture

- **(A) Solver — read-only.** Model day(s) as pods (time, commercial capacity, occupants);
  constraints = per-advertiser separation + pod capacity. Search for a move-set (relocate
  moveable WL spots, same-day-first) that seats the stuck spot with all constraints still
  satisfied. Output: proven move plan, or "genuine under-delivery → make-good." No DB writes.
- **(B) Applier.** Execute the proven plan, mirroring SE: blacklist blocker (preserve
  `ID_FILMATI`), seat stuck spot, re-place blocker (**ideally via Etere's own reschedule
  stored proc** if one exists), handle checksum/metadata to avoid yellow triangles.

## Research findings (RESOLVED 2026-06-26)

**Break/pod = a `traffic_segment` row with `Type='COMS'`.**
- Capacity = `traffic_segment.MaxDuration` (frames); per-date override in `trf_instancesegment`
  (prefer it when a row exists for the date; none existed for 6/30). `Duration` = current used.
- Absolute air position of a break = `traffic_scheduleblock.Offset + traffic_segment.Offset` (frames).
- Join chain: `Traffic_Calendar`(Cod_User, Date, Level=0 → ID_TrafficSchedule) → `traffic_scheduleblock`
  (→ ID_TrafficBlock, Offset) → `traffic_block` → `traffic_segment` (COMS, MaxDuration, visible=1).
- **Spot→pod link: `trafficPalinse.clusterIndex = traffic_segment.ID_TrafficSegment`.**
  (`trafficPalinse.id_fascia` = the *block* id, NOT the segment — don't use for capacity.)
- Commercials vs program: `trafficPalinse.ID_ContrattiRighe > 0` (TYPE is uniformly 'T'; don't rely on it).
- **"Fits" = `MaxDuration − SUM(used DURATION) ≥ spot duration`.** Solver must compute fit itself —
  the insert procs are encrypted; unknown if they enforce capacity.
- Concrete 6/30 NYC 20:00–23:00: 14 COMS breaks, cap 1981s / used 420s / **FREE 1560s (~52 :30 slots)**,
  incl. 4 fully-empty breaks (e.g. the 22:26 break, 240s, where the pilot's 14th spot landed).

**Stored procs (bodies WITH ENCRYPTION — signatures only):**
- Insert a spot into a break: **`Traffic_InsertEvent`** (proven in-repo, see below) and richer
  `Traffic_InsertEventX_C` (adds explicit @newtype/@duration). Coordinates: schedule, block, segment,
  ora, id_filmati, duration.
- **`Trf_MoveEventToBlankSpace`** (@DATE,@cod_user,@id_Block,@id_segment,@orap,@priorita,@idTpalinseSource…)
  — built-in single-spot relocator. `Trf_MoveEvent`(@src,@dst tpalinse) = move by ids.
- `Trf_DeleteForRescheduleDetail` / `...ByDate`(@idcontrattirighe,@coduser,@date,…) — Etere's own
  "remove a line's placements for rescheduling" (proc-level mirror of our blacklist DELETE).
- `Traffic_AssignAsset_C`(@id_trafficPalinse,@id_filmati,@coduser) — attach/preserve creative on a placed event.
- `tpalinse_restore`(@id,@level) — un-trash a TPALINSE row.

**Code precedents to reuse:**
- `src/business_logic/services/daily_programming_run.py` — `_insert_event()` already calls
  `EXEC Traffic_InsertEvent …` and reads back new id_tpalinse; `_slots()` enumerates COMS/PRGS
  segments preferring `trf_instancesegment` over `traffic_segment`. **Reuse both.**
- `src/web/routes/orders.py` — `worldlink_room_blacklist` (~L1197): pull-a-spot flow that preserves
  `ID_FILMATI`, DELETEs trafficPalinse+TPALINSE, writes Traffic_ScheduleList; `worldlink-room/spots`
  (~L1165): placed-spot listing with line/contract context.

## Worked example (pilot, done by hand 2026-06-26 — solver's first test case)

Tue 6/30, line 80023, place the 14th spot. Manual steps + confirmed end state:
1. Removed the moveable blocker **Feeding America / WL Direct 125346, 120s @ 21:58** (blacklist, creative kept).
2. Nudged one 4Imprint spot **22:42 → 22:57**.
3. Ran Etere "reschedule in booked segments" → it **reshuffled multiple spots**: moved the old 22:12
   spot into the freed **21:58** pod AND placed the stuck 14th in the empty **22:26** break.
4. Rescheduled the removed blocker → it re-placed **same day at 06:31** (window 06:00–23:59), creative intact.

End state (4Imprint advertiser-wide evening): 21:38 · 21:58 · 22:26 · 22:57 — all ≥20 min. 80023 now 14/14.
Lesson: Etere's reschedule moves >1 spot to satisfy sep; a feasible shuffle definitely exists here, so the
solver must find *a* feasible assignment (not necessarily Etere's exact one).

## Pilot

- Contract **WL Marketing 215720** (`ID_CONTRATTITESTATA = 2915`), **NYC only**.
- Only stuck NYC line: **80023** (L2), M–Th 8–11p, flight 6/29–7/2, 14 ordered / 13 placed
  (Mon 3, Tue 2, Wed 3, Thu 5), sep (20,0,0), :15 paid, advertiser 4Imprint.
- 4Imprint also has contract **WL Marketing 215721** in this window → sep spans both.
- Day to crack first: **Tue 6/30** (fewest placed). Moveable example:
  **Feeding America / WL Direct 125346**, 120s @ 21:58, window 06:00–23:59, M–Su, creative attached.

## Solver model (built + validated 2026-06-26) — `browser_automation/spot_relocator.py`

Read-only. `solve_line(cur, line_id)` → for each eligible date calls `pack_day` (backtracking CSP):
seat the advertiser's in-window spots **+ the new one** into distinct COMS pods that are pairwise
≥ sep apart (advertiser-wide), each within its line's window, with pod capacity respected. There are
TWO move types:
1. **Advertiser-repack** — reposition the advertiser's own (all-WL) spots across pods to open a
   sep-valid slot for the new one. (Implemented.)
2. **Capacity-eviction** — when the enabling independent-set pod is *full*, evict a **moveable WL
   occupant** (esp. wide-window Direct Donor fillers) to create capacity, then repack, then re-home
   the evicted spot (same-day-first), keeping its creative. (NEXT — mirrors the manual fix.)

**Validated on line 80024 (CMP, 4Imprint, the 80023 mirror):** placing the 14th needs a 7-pod set
(20:20·20:48:30·21:18·21:38·21:58·22:26·22:57); pure repack is infeasible because the **21:38 pod is
120/120 full with WL Direct 125304 (Shriners Hospital, Direct Donor, 120s, creative=Y)**. Evicting it
+ repack + re-home = the solution — identical shape to the NYC worked example. Solver currently
reports this correctly as "infeasible without eviction"; eviction is the next increment.
Gotcha proven: the greedy max-independent-set ceiling is capacity-blind (counted 21:38) — real
feasibility must check `avail = MaxDuration − other-advertiser usage` per pod, which the CSP does.

## Plan (checkable)

- [x] Research break/capacity model + reschedule stored procs (done 2026-06-26 — see findings above)
- [x] Capture a hand-worked example as the first test case (Tue 6/30, line 80023 — see above)
- [x] Build read-only Solver core (pack_day CSP: pods+capacity+per-advertiser sep, advertiser-repack);
      validated against line 80024 — correctly identifies the eviction-needed case
- [x] Add capacity-eviction move (single-level): evict freest moveable WL occupant from a needed full
      pod + re-home same-day lowest-contention, keep creative. **Validated on line 80024** — solver
      auto-produced the same plan as the NYC hand-fix: evict Shriners Direct Donor 120s from 21:38 →
      re-home 11:41:30; repack 4Imprint to 20:20·20:48:30·21:18·21:38·21:58·22:26·22:57; seat 14th at 22:57.
- [x] Wrap solver in a `/scripts/spot-relocator` web page (read-only): route + API
      (`/api/scripts/spot-relocator/analyze`) in orders.py, template `scripts/spot_relocator.html`,
      `plan_line`/`analyze_contract` JSON funcs in solver. Card added to scripts.html as
      **`module-card soon` + `in-progress` badge, non-clickable `<div>`** so users can't open it
      (page still reachable by direct URL for testing). Verified API on contract 2915: 8 stuck lines
      → 7 evict+repack, LAX make-good.
- [ ] Multi-shortfall loop (place one → recompute → next) — currently plans 1 spot/line
- [ ] Build the Applier (writes) — see below
      (advertiser = COMMITTENTE, across all their contracts); for each stuck spot, search for a
      feasible move-set (relocate moveable WL spots, same-day-first). Validate it can reproduce
      *a* feasible shuffle for the pilot day. Show plan vs Lee's intuition. NO writes.
- [ ] Build Applier: pull blocker preserving ID_FILMATI (reuse worldlink_room_blacklist pattern) →
      insert via Traffic_InsertEvent (reuse daily_programming_run._insert_event) / Trf_MoveEventToBlankSpace →
      re-place displaced spot → checksum/metadata handling. Test on a scratch date first
      (insert procs are encrypted; confirm they don't overfill capacity).
- [ ] Pilot end-to-end on a fresh stuck contract, NYC; verify in scheduler
- [ ] Generalize beyond WL-only / single market once proven

## Notes / gotchas
- Separation is **per-advertiser**: e.g. a Drive DeVilbiss spot at 22:12 next to a 4Imprint spot at
  22:26 is fine (different advertisers). Build each advertiser's own timeline.
- Etere's "reschedule in booked segments" moves >1 spot to satisfy sep — our solver only needs to
  find *a* feasible assignment, then the Applier writes it deterministically.
- Insert procs are `WITH ENCRYPTION`; solver must enforce `MaxDuration − used ≥ duration` itself.
