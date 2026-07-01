# Break Optimization — PI duplicates resolved in-place (no cross-break moves)

## Request
On `/master-control/break-optimization`, PI-spot optimization currently pulls a
same-length PI out of another break up to ±1 hour away and swaps the two. The
team wants **all PI switching kept within the program** — nothing moved back/
forward an hour. Instead, when a break has duplicate PIs, replace one of the
duplicates **in place** with a valid same-length PI from the FILMATI library.

## Decisions (confirmed with user)
- Remove the cross-break / ±1hr swap entirely; resolve duplicates via in-place
  library substitution only. Nothing moves between breaks or times.
- Duplicate detection stays at the **break (pod)** level (matches today).
- Library pick: **PI first, PSA fallback** at the same length (±5 frames), not
  expired, excluding `DO NOT%`, and excluding any product key already present in
  that break (so we never create a new duplicate). PSA only used if no PI fits.
- If nothing fits at that length → leave both duplicates, flag `pi_unresolvable`.

## Mechanism (grounded in existing code)
- `TPALINSE.ID_FILMATI` holds the creative. In-place swap = one UPDATE of the
  creative-identity fields, mirroring auto-assign at orders.py:1370 and the
  filler pattern at orders.py:2200-2268:
  `UPDATE TPALINSE SET ID_FILMATI, COD_PROGRA, NEWTYPE, TITLE, SUPPORTO WHERE ID_TPALINSE=?`
  Keep the original ORA / XORDER / DURATION (library match is within ±5 frames,
  so downstream timing is unaffected — no re-timing needed).

## Changes

### 1. `_bo_fix_pi_conflicts` → `_bo_resolve_pi_duplicates(cur, breaks)` (orders.py ~3416)
- Take a DB cursor (needs the library query).
- For each break, detect duplicate PI product keys (same `_pi_product_key`).
- For each duplicate, query FILMATI for a replacement of the same duration:
  PI first then PSA, `ABS(DURATA - dur) <= 5`, `DATA_SCAD` valid, not `DO NOT%`,
  and `_pi_product_key(DESCRIZIO)` not already in the break. `ORDER BY NEWID()`.
- On match: rewrite that optimized spot's identity fields in place and attach
  replacement payload keys (`replace_filmati_id`, `replace_title`,
  `replace_cod_progra`, `replace_newtype`) + a `pi_replacement` diagnostic
  (`"PI-504-030 → PI-497-030"`). Recompute `changed`/`violation`.
- No match at that length → `pi_unresolvable = True` (leave both).
- Delete the ±1hr `one_hour` cross-break search block and `pi_swap_source`.

### 2. `_bo_process_market` (orders.py ~3729)
- Call `_bo_resolve_pi_duplicates(cur, breaks)` instead of `_bo_fix_pi_conflicts(breaks)`.

### 3. Apply endpoints (orders.py ~3762 apply, ~3803 bulk-apply)
- When an update carries `replace_filmati_id`, add the creative-swap UPDATE
  (ID_FILMATI, COD_PROGRA, NEWTYPE, TITLE, SUPPORTO) alongside the existing
  ORA/ORA_P/XORDER write. bulk-apply reads `brk["optimized"]` directly (already
  has the fields); apply reads them from the posted `updates`.

### 4. Template `break_optimization.html`
- Frontend must forward the `replace_*` fields in the apply POST body.
- Show the replacement in the break diff (e.g. badge "↻ replaced PI-504-030 →
  PI-497-030") and keep the `pi_unresolvable` warning wording.

## Verification
- Load a date/market with a known duplicate-PI break; confirm the plan now shows
  an in-place replacement (same time slot) instead of a cross-break swap, and no
  spot's ORA changes except the intended pod ordering.
- Confirm a :15 duplicate with no library match flags `pi_unresolvable` and is
  left untouched.
- Apply on a single break; verify TPALINSE row's ID_FILMATI/TITLE changed and
  ORA/XORDER unchanged; re-load shows no remaining duplicate.
- Bulk-apply dry check across markets: counts reflect replacements.

## Review

Done — all edits in `src/web/routes/orders.py` + `templates/master_control/break_optimization.html`:
- `_bo_fix_pi_conflicts` (±1hr cross-break swap) removed → `_bo_resolve_pi_duplicates(cur, breaks)`
  + `_bo_pi_library_pick(cur, dur, exclude_keys)` (in-place library substitution).
- `_bo_apply_pi_replacement(cur, u, id)` writes the creative swap on apply + bulk-apply.
- Template: `↻ PI Replaced` badge/panel, `↻` spot marker, replacement fields forwarded in
  applyBreak + applyAll. Removed obsolete `pi_conflict_detail`/`pi_swap_source` UI.

Verified (read-only, live DB via load endpoint, no writes):
- NYC 2026-07-01 Break @9:25 — dup PI-504 → `PI-504-060` replaced with `PI-497-060: eZwell`
  (:60, filmati 119139), ORA unchanged, payload complete.
- SFO 2026-06-30 Break @8:58 — exact dup `PI-481-030`×`PI-481-030` → one replaced with
  `PI-505-030: Alien Power` (:30, filmati 136877), ORA unchanged.
- Library inventory: :30=8 PI, :60=20 PI, :15=0 PI/8 PSA (PSA fallback path exercised on :15).
- orders.py byte-compiles; no leftover refs to removed fields.

NOT executed: the apply write against live Etere (mutates on-air scheduling). Code mirrors the
proven auto-assign `UPDATE ...ID_FILMATI` (line ~1370) and blacklist filler pattern. Recommend
the team click **Apply Fix** on one break in the UI as the final confirmation.
