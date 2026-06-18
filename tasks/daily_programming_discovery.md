# Daily Programming — Discovery & Design Reference

Discovery session 2026-06-18 (Korean News / NEWSTODAY worked example). This is
the authoritative reference for building the **Set up Daily Programming** tool.
Goal: insert a program (mp4 / `filmati`) into the existing program-guide slots
for a network's markets — replacing the manual Etere drag/Explode/fix workflow
with a deterministic direct-DB write.

---

## 1. Tool scope & UI

- Lives on `/master-control` (card, "In Progress") → page `/master-control/daily-programming`
  (route in `src/web/routes/orders.py`, template `master_control/daily_programming.html`).
- Does **NOT** author the weekly program grid (those blocks already exist in
  Etere). It drops the *program content* into existing slots.
- **Network → market fan-out** (`_DP_NETWORK_CODUSERS`):
  - Crossings TV (`CTV`) → COD_USER **1,2,3,4,5,6,7,8,9**
  - The Asian Channel (`TAC`) → COD_USER **10**
- COD_USER ↔ market: 1=NYC 2=CMP 3=HOU 4=SFO 5=SEA 6=LAX 7=CVC 8=WDC 9=MMT 10=DAL
- Per-market **selection** UI is built (pills + single-market focus, `mode:"market"`).
  Per-market *content override* (e.g. SFO-only cut) is deferred.

## 2. Source of truth — weekly Excel grids (K: drive)

`K:\Programming\! <Network>\<year>\<MM yyyy>\<Network> Programming Schedule <YYYYMMDD> (with Comcast).xlsx`
- `<YYYYMMDD>` = the **Monday** of the M–Su week; folders are broadcast-month; `!!!` prefix = month being built.
- Sheet "Local Channels": row 2 = MON–SUN, row 3 = dates (C2=Mon…C8=Sun), col A/I = LOCAL TIME (30-min rows), cells = program + language.
- **Programs are MERGED cell ranges**; block end time = the time label of the row *just past* the last merged row.
- Korean News = "MBC Newsdesk (Korean News)", **M–F 08:00–09:00**.
- Future: build our own program-grid DB; Excel becomes an export from it.

## 3. The asset (FILMATI)

- Korean News asset: `FILMATI.COD_PROGRA = 'NEWSTODAY<MMDDYY>'`
  (6/17 = `NEWSTODAY061726` ID 140920; 6/18 = `NEWSTODAY061826` ID 141041), `TIPO='T'`,
  `DURATA` in frames @ 29.97fps.

## 4. EDL markup model (how a program is "conformed")

Two tables, keyed by `ID_FILMATI` + `VERSION`:
- **`FEDLDESCRIPTION`** — EDL header: `SOM`/`EOM` (overall in/out, frames), `DURATION`, `TESTO` label ('EDL no.1' / 'EDL n.2').
- **`FINTERRUZIONI`** — the marks: `MARKIN`/`MARKOUT`, `INSERTION_POINT`, `BULK_VIDEO`, `TO_EXPLODE`, `VALID`, `VERSION`, `MARKORDER`, `FLAG`.

**`VERSION = VideoStandard_id × 1,000,000 + (EDL_slot − 1)`** (resolved live):
- EDL slot 1..9 = low digits; video standard = millions.
- NTSC(VS 0) EDL1 = VERSION **0** (= what CTV airs — the row we read/write).
- SOM/EOM are file-global (one playback window) but stored per video standard;
  one edit updates all VERSION rows to the same wall-clock time (29.97/30/24/50/60…), synced via `VSLinked`.
- "version" is overloaded: this DB column ≠ Etere's asset-versioning ("version 1" in props, never used).

**Mark types:**
| Type | MARKIN vs MARKOUT | BULK_VIDEO | INSERTION_POINT | FLAG |
|---|---|---|---|---|
| Pure split | equal (in==out) | 0 | 1 | `P` |
| Black / omit | range (in < out) | 1 | 0 | `''` |

**Drop-frame timecode → frame:** `frame = (HH·3600+MM·60+SS)·30 + FF − 2·(totalMin − ⌊totalMin/10⌋)`.

**Multi-EDL = native per-market variant** (e.g. NYC keeps provider breaks via EDL1; other markets omit via EDL2). The hook for the deferred per-market override.

**Direct DB write/delete to these tables is reflected in Etere's Media UI on refresh** (confirmed). So markup can be fully automated. (When filtering by slot, do modulo in Python, not SQL `%`; `conn.commit()`.)

## 5. Edius CSV import (deferred build)

"EDIUS Marker list" v3 CSV (`#` header lines, cols `No, Anchor, Position, Duration, Comment`; Position = drop-frame TC; CRLF). Validated: markers matched hand entry.
Import rule (splits-only): parse Position→frame; keep `0 < frame < EOM`; **drop any marker == EOM** (no-op); write each as a pure split on EDL 1 across the VERSION set. (5 markers → 4 splits → 5 parts.) Deferred: use `Comment` to distinguish split vs black, and in-point/SOM.

## 6. Schedule structure (TPALINSE)

- **`traffic_segment.Type`**: `PRGS` = program segment (our target), `COMS` = commercial segment.
- **`TPALINSE.NEWTYPE`**: `PGM` = program content, `COM`/`BNS` = spots, `NOOP` = empty placeholder (carry `LIVELLO=666` = deleted/ghost level), `ID`/`PER` = station id/other. Live rows = `LIVELLO=0`.
- **`EVENT_TYPE`**: `T` = sequential (floats to where prev asset ended); `F` = fixed/locked to a timecode (hard start, clips what precedes).
- **`XORDER`** = intra-`ORA` play-order key (lower = earlier). Skeleton uses round values ~10,000 apart; inserts/moves get `floor((prev+next)/2)`. Ties fall back to `ID_TPALINSE`. `EVENT_TYPE` does NOT affect order — XORDER alone.
- **Fill-to-the-hour**: program + breaks toward 60:00, leave ≤10s, drop a 25s **Station ID** (cut off) as last `T`; the next hour's program is `F`-locked to the top of the hour and clips it.

## 7. Korean News hour anatomy

```
OPEN bumper (F, 08:00:00, ~14s)  →  Pt1 (T) → COMS break → Pt2 → … → Pt5
  →  CLOSE bumper (T, floats to end, after last Pt)  →  commercials  →  Station ID (T)
  →  [next hour program (F) at 09:00 clips the tail]
```
- Bumpers = `FILMATI COD_PROGRA='BUMP_<SHOW>_OPEN'/'..._CLOSE'`, `NEWTYPE='PGM'`. Master control pre-places them (OPEN in first market only; CLOSE in every market). Most shows have none.
- Piece one of a program is `F`-locked to the grid start time; for Korean News the OPEN bumper is that piece one.

## 8. Explode mechanics + the bug (why we go deterministic)

- Placing the marked-up file = ONE `PGM` row (whole file, TC SOM→EOM, `EVENT_TYPE T`) at top of break. It inserts ABOVE the open bumper → must reorder bumper first (give bumper a lower XORDER).
- **Explode** splits that row using VERSION-0 marks: original row → Part 1 (TC_O shrinks to first split, PART 0→1); new rows for Parts 2..N with contiguous `TC_I/TC_O`, `NEWTYPE='PGM'`, `EVENT_TYPE='T'`, `PART=n`.
- **Bug:** Explode places the floating CLOSE bumper inconsistently (sometimes before the final segment) because the final segment can land at a midpoint XORDER *above* the pre-placed close bumper, and live-vs-live XORDER ties resolve by `ID_TPALINSE` (creation order) → looks random. Always needs manual verify/fix.
- **Manual fix observed:** move the final segment's XORDER below the close bumper (back into its placeholder slot), then a schedule **RECALC** recomputes all floating `T` ORAs from XORDER order (Pt1→08:00:14, …, CLOSE floats to right after last Pt, before 09:00).
- Remaining duplicate XORDERs after fix are **live(0) vs dead(666) ghosts** — exactly one live row per XORDER → harmless (666 don't air). Collisions only matter live-vs-live.

## 9. Deterministic build plan (target)

Per market (fan across the network's COD_USERs):
1. Ensure the file's EDL 1 (VERSION 0 + sibling standards) has the correct window (EOM) + split marks (from hand entry or Edius CSV import).
2. Write the program rows directly into the PRGS segments of the target date/hour:
   - OPEN bumper first (lowest XORDER, `F` at slot start) if the show has one.
   - Program parts in order, each its own distinct XORDER, contiguous timecodes.
   - CLOSE bumper strictly after the last part (XORDER > last part), `T`.
   - Distinct XORDERs to avoid live-vs-live collisions (ghost 666 rows can be ignored/cleaned).
3. Trigger / replicate the schedule RECALC so floating ORAs settle.
4. Skip Etere's Explode entirely → no random bumper placement, no per-market fix.

## 10. Still OPEN (not yet worked out)

- Full from-scratch `PGM`-row column set (`EVENT`/`EVENT_P`, `ORA` derivation, `DSK*`, `STATUS`, `TRAFFICID`, etc.).
- How to trigger Etere's schedule RECALC programmatically (SP/HTTP).
- The operator's exact manual resolution flow (to be captured in their words).
- Actual multi-market fan-out (only NYC built so far in the worked example).
