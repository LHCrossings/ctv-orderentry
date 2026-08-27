# "Finish" — fill the true remainder of a broadcast hour (spec v0.2 — all 8 questions answered 2026-08-27; design next)

Closes the master-control loop: Daily Programming places pieces + fillers, Break
Optimization orders breaks, **Finish** makes the clock reach the top of the hour.
Replaces Etere's blind COMS filler pass (PI :30/:60 stuffed into COMS by capacity,
then re-sorted by BO) with a deliberate fill computed from what is actually placed.

Reference hour: NYC 2026-08-27 08:00–09:00 Korean News (`tasks/` discussion) —
show ends 08:54:18, UNIAE1667 filler 4:35.88, end break :15 COM + :30 PI + :15 PSA
+ 25 s ID, ID starts 08:59:53.86 (6.1 s airs before the 09:00 type-F cut).

## Lee's rules (as stated)
0. **EVENNESS is the objective** (Lee, closing remark): whoever fills — Etere or
   Finish — a show's breaks should be nicely even; no break incredibly longer than
   the others; the final break ≤ 2:30. Fill is therefore a DISTRIBUTION problem
   (place PIs into the shortest interior breaks until the final break ≤ 2:30 and
   break lengths are balanced), then the PSA/ID end game at the top of the hour.
   **The final break must never be the LONGEST break** (Lee, 8/27, after seeing
   the prototype put 2:25 at the end): a PI lands in the final break only if it
   stays ≤ the longest interior break; otherwise shortest interior break first.
1. **Structure** COMS/PRGS exists through June; orders land in COMS; Daily
   Programming fills PRGS. The show's Etere length is a PLACEHOLDER (exact for
   Punjabi, AVS, Namaste; not in general), so the real remainder is only known
   once everything is placed back-to-back (what Strategic Editor shows).
2. **Precondition:** all commercials and all program pieces for the hour are
   placed. Finish is the last click.
3. **Large gap** (minutes) → a program filler (UNIAE-style pool from Daily
   Programming's "Add filler"). Rare in Korean News, common elsewhere.
4. **≤ ~3 min** → tiers, in order: **PI spots** (:30/:60) → **PSAs** (:10/:15)
   → target **< 10 s left** → **Station ID** for that network/market.
5. **Station ID** is intentionally 25 s; the next hour's first event is type F
   (fixed) and cuts it at the top of the hour. **≥ 5 s of the ID must air.**
   So the pre-ID remainder must land in **[5 s, 25 s]**, preferring < 10 s.
6. **ID is per HOUR, not per show.** Two half-hour shows need one ID (at the
   hour), unless a small unfillable gap (e.g. 7 s) at the half-hour wants one.
   Master control currently places one after EVERY show — stop that.
7. With Finish in place, PI placement no longer needs Etere's filler pass, and
   BO no longer needs to reason about PI ordering — Finish places PIs in a
   rational order itself.

## Algorithm sketch
```
for each (market, date, hour-block [or show boundary]):
  events = active TPALINSE rows in window, packed back-to-back (SE view)
  remainder R = hour_end − end(last event)          # true gap
  if R > BIG (e.g. > 3:00):   place program filler(s) from the pool, R -= dur
  while R − 25s > 0 and a PI fits (:60 then :30, rotation rules): place, R -= dur
  while R > 10s and a PSA fits (:15 then :10) leaving R' ≥ 5s: place, R -= dur
  if R < 5s: swap a :30 PI in this hour → :15 PSA + :10 PSA  (R += 5s)
           (no :30 PI? swap a :60 PI → :30 PI + :15 PSA + :10 PSA, also +5s)
  place Station ID (25 s) for the market; the next hour's EVENT_TYPE='F' cuts it
  half-hour boundary: R ≥10 keep filling; 5–10 ID; <5 flip next show's first
    piece EVENT_TYPE F→T and carry the ID to the top of the hour
  conform XORDER for the window (same-multiset reassignment as Daily Programming)
  SUPPORTO for PI/PSA = prefix + FS_FILMATI.FILE_ID (never DESCRIZIO)
```
Writes: TPALINSE inserts only (fillers/PI/PSA/ID carry no contract line, so no
trafficPalinse). Delete-and-verify inside one transaction; restore SQL first.

## Open questions for Lee
- ~~Where does the slack go?~~ **ANSWERED (Lee):** at the END, after the show's
  close bump — that is where all PIs/PSAs go. Ideal final break ≤ ~2:30. If it
  would be ≥ 3:00, spill PI spots back into the show's interior COMS breaks to
  space things evenly. Today's UNIAE filler was the exception (a story was cut);
  normally Korean News is trimmed toward 50:00 instead of padded.
- ~~PI selection order.~~ **ANSWERED (Lee):** rotate EVENLY through all active PIs
  (least-recently-aired first, per market, state derived from TPALINSE). Never
  air the :30 and :60 of the SAME campaign (`PI-nnn`) adjacent to each other.
  :30s are chronically scarce so they recycle faster — rotate per length pool.
  All PIs may air in all markets (no exclusions).
- ~~PSA language.~~ **ANSWERED (Lee):** all PSAs are English → any PSA fits any
  block/market. Rotate evenly like PIs (:15 and :10 pools).
- ~~"Appropriate Station ID"~~ **ANSWERED (Lee) + verified in FS_FILMATI:** generic
  everywhere except the OTA markets. COD_USER→asset: 1,2,3,5,6,8,9 → **67910**
  `ID - NEW - GENERIC` 25.09s; SFO 4 → **67911** `ID - NEW - SFO ONLY`; CVC 7 →
  **67909** `ID - NEW - CVC ONLY`; DAL 10 → **83129** `ID - TACDAL - GENERIC` 25.03s.
  (142947/142948/83128 `… - FCC` are the once-daily end-of-day IDs placed by the
  existing FCC job — a different rule; `ID - CHANNEL CHANGE -*` :30s are unused.)
  Usage 8/20–8/27 ≈ 22 IDs/day/market = the per-SHOW habit to retire.
- ~~Half-hour shows.~~ **ANSWERED (Lee):** at a half-hour boundary, after PI/PSA
  fill: gap ≥10s → keep filling (:10 PSA fits); gap 5–10s → place the ID there
  (airs 5–10s, cut by the next show's type-F start); gap <5s → NO ID: flip the
  next program's FIRST piece from type **F → T** (follows previous) so it bunches
  up, and the hour's ID goes at the end of that second show, at the top of the
  hour ("as close to the top of the hour as possible" = the FCC requirement).
  Mid-hour IDs are otherwise unnecessary — one ID per HOUR. VERIFIED: the F/T flag is
  **`TPALINSE.EVENT_TYPE`** ('F' on the 09:00 program, 'T' elsewhere) — NOT `TYPE`
  (every row is TYPE='T'). Also `ORA_P`/`DURATION_P` mirror ORA/DURATION.
- ~~Remainder < 5 s~~ **ANSWERED (Lee) — top-of-hour end game:** gap >10s → PSAs
  (:15/:10) until ≤10s (15s → :10 PSA + ID airing 5s); gap 5–10s → ID only; gap
  <5s → swap one :30 PI (anywhere in the hour) for a :15 PSA + :10 PSA — frees
  exactly 5s — then the ID lands in 5–10s. If the hour has NO :30 PI, swap a :60
  PI for :30 PI + :15 PSA + :10 PSA (55s, also frees 5s). Never air an ID
  shorter than 5s.
- ~~Scope of v1.~~ **ANSWERED (Lee):** unit of work = **per PROGRAM** ("finish per
  program"); we'll try different ways. UI undecided — new page (like Break
  Optimization) or a button inside an existing utility, with a "finished"
  indication (gray-out) per show. **DECIDED (Lee):** new page
  "Fill & Finish" in the BO Log-Version interaction pattern — network/market
  pills + date, day's shows listed log-style, expand → packed timeline with
  remainder, one Finish button per show; finished badge DERIVED from the data
  (ID present + gap ≤10s), never a stored flag, so EE edits stay honest.
  A day is NEVER finished at once — programming arrives staggered — so no
  whole-day action. **Re-runnable:** Finish must recognise its own prior fill
  (PI/PSA/ID rows after the close bump — NB Etere filler rows DO carry a trafficPalinse row with ID_ContrattiRighe=-1, so freestanding ≠ 'no trafficPalinse'; ownership = our own tag),
  remove it, and recompute — never stack a second fill.
- ~~Kill the Etere filler pass~~ **ANSWERED (Lee):** Etere's COMS filler is a
  MANUAL function master control runs. Nothing to switch off: Finish treats
  whatever is already in the timeline (paid, program pieces, Etere/operator PIs)
  as GIVEN and fills only the true remainder. If MC didn't run it, Finish does
  the whole job. MC stops running it when they trust the page.
  **Ownership tag:** TPALINSE.PROVENIENZA is 'TRAFFIC_NEW' for scheduler/traffic
  rows (incl. Etere filler PIs: 794 PER) and BLANK for hand-placed EE rows (ID 128,
  PER 104, PSA 100, NOOP). Finish writes its own rows with PROVENIENZA =
  'CTV_FINISH' so a re-run removes exactly its own fill and nothing else
  Caveat: our own break-opt filler INSERT (orders.py ~4708) writes
  'TRAFFIC_NEW' to mimic Etere, and Daily Programming goes through
  `Traffic_InsertEvent` (SP sets PROVENIENZA itself) — a custom PROVENIENZA is
  untested. Safer tag: **`NOTE='CTV_FINISH'`** (NOTE is blank on every row seen)
  and keep PROVENIENZA='TRAFFIC_NEW'. Decide at design time; test on one row.

## Next steps
1. Design doc: data model of a "program window" (open bump → close bump → next
   show's F event), remainder math from packed ORA, PI/PSA rotation state,
   swap logic, half-hour F→T flip, XORDER conform, restore SQL.
2. Read-only prototype: compute + PRINT the fill plan for NYC 8/27 08:00 and a
   half-hour pair, compare with what Lee did by hand. No writes.
3. Page: Fill & Finish (BO log-version pattern), finished badge derived.
4. Write path behind a confirm, one show at a time; first live click = Lee's.
