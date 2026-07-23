# Weekend Korean-drama setup (CTV) — Drama selectors + duration-based filler fill

Automate the weekend Korean-drama setup that master control does by hand today.
Weekday (M-F) behavior is UNCHANGED (already shipped: KD-* Piece A → auto K-FILLER
into blank slots).

## Discovered facts (verified live, 3 weeks)
- **Episode repeat pattern**: within a broadcast week, the weekend re-airs that
  week's weekday episodes — **Sat = Mon+Tue+Wed**, **Sun = Thu+Fri** (one holiday
  week drifted → pre-fill must be overridable).
- **Block structure** (08:00–10:00, 2h = PRGS + COMS, all 9 CTV markets, same
  episodes everywhere):
  - **Saturday**: 9 PRGS slots (90 min) = 3 episodes × 3 pieces. COMS 30 min.
  - **Sunday**: 6 PRGS slots (60 min) = 2 episodes × 3 pieces. COMS 60 min ("massive break").
- **Programming budget is CONSTANT** = Saturday's PRGS total (~90 min), both days.
  Sat: 3 dramas (~87m) + ~1 filler. Sun: 2 dramas (~59m) + ~31m fillers to reach 90m.
- **Filler placement**: fillers stack into the LAST PRGS slot (Sat slot 9 / Sun
  slot 6), after the last drama piece, floating (T). Over-stuffs the slot.
- **COMS is NOT touched** — commercials are arranged later in EE to conform to
  whatever programming time is left (foundation in MG, cleanup in EE).

## Plan

### 1. Trigger + modal (daily_programming.html)
- [ ] Detect a weekend (Sat/Sun) Korean-drama grid row on CTV → open a new
  **weekend drama modal** instead of the weekday file picker.
  (Confirm the grid row's title/kind for the drama against a real CTV sample grid.)
- [ ] Modal shows **Drama selectors**: Saturday = Drama 1/2/3, Sunday = Drama 1/2,
  each mapping to PRGS blocks 1-3 / 4-6 / 7-9. Each selector = a Piece-A picker
  (reuse the super-search), **pre-filled** from the episode pattern, overridable.

### 2. Episode pre-fill (route)
- [ ] `GET …/kdrama/weekend-episodes?date=` → for the weekend date, look up the
  KD-* Piece A that aired on that broadcast week's Mon/Tue/Wed (Sat) or Thu/Fri
  (Sun) — from TPALINSE — and return them as the pre-filled selectors.

### 3. Duration-based filler draw (filler_rotation.py)
- [ ] `draw_until(conn, target_frames)` — draw random unused K-FILLERs (same
  cycle-through rotation) accumulating DURATA until cumulative ≥ target; return
  the list. (Weekday `draw_n` unchanged.)

### 4. Weekend placement (daily_programming_run.py)
- [ ] `place_weekend_drama(conn, cod_user, d, block, dramas, fillers, pending)`:
  - Resolve PRGS slots in the 08:00–10:00 block.
  - Place each drama's A/B/C one-per-slot in block order (Drama 1 → 1-3, …).
  - Stack the drawn fillers into the LAST PRGS slot after the last piece (floating T).
  - First piece overall = F anchor; rest = T. `_rebuild` + `_sync_checksums` +
    verify-gate (commit/rollback). COMS untouched.
  - Programming target T = the Saturday block PRGS total; on Sunday read the
    paired Saturday (d−1) block's PRGS total. Fillers fill (T − drama duration).

### 5. Run route + rotation commit
- [ ] `POST …/kdrama/weekend-run` (or extend run): fan across the 9 CTV markets
  (same episodes+fillers everywhere — a filler is shared across markets), each
  market transaction-safe. Draw fillers ONCE, apply to all. Mark drawn K-FILLER
  codes used on success (dedup).

### 6. Verify (live, safe)
- [ ] Reversible test on a real weekend day: place → confirm block mapping +
  filler stack in last slot + programming total ≈ Saturday budget; restore.
- [ ] Pre-fill lookup returns the correct Mon/Tue/Wed / Thu/Fri episodes.
- [ ] Weekday path untouched; CTV-only.

## Assumptions to confirm before building
1. Programming budget = Saturday's PRGS total (~90m), derived on Sunday from the
   paired Saturday block (d−1). Auto-adjusts if the block is reconfigured.
2. Fillers all stack into the LAST PRGS slot (Sat 9 / Sun 6), after the last piece.
3. Same episodes + same drawn fillers across all 9 CTV markets.
4. Tool does the MG foundation only; COMS commercials stay an EE task.
5. Pre-filled episodes are overridable (holiday weeks).

## Review (2026-07-23) — built + live-validated
- Episode repeat pattern confirmed (Sat=Mon/Tue/Wed, Sun=Thu/Fri; holiday weeks
  drift → pre-fill overridable). Weekend fillers are PURE RANDOM by duration (no
  rotation token, per Lee) via `filler_rotation.draw_until` (fits the gap, minimal
  overshoot). Budget = Saturday PRGS total (~90m), derived on Sunday from paired
  Saturday (d−1). Compute model reproduces real filler counts exactly (Sat→1, Sun→3).
- `place_weekend_drama` live-tested reversibly: Sun 8/2 (2 dramas + 3 fillers
  stacked in last slot) and Sat 8/1 across NYC+CMP (3 dramas blocks 1-3/4-6/7-9 +
  1 filler), first piece F / rest T, verify-gate commit, fully reversed (window→0,
  no ghosts).
- Routes: kdrama/weekend (prefill+budget), .../fillers (preview+reroll),
  .../run (fan across CTV markets, sequential, same episodes+fillers). UI: weekend
  modal with Drama selectors + filler preview + reroll + run. Weekday path & other
  networks untouched. ruff + py_compile clean.
- NOT exercised from WSL: grid read + full modal (runs on Windows server). No new
  table (weekend fillers don't use the rotation). Recommend Lee's first live
  weekend run as final validation (verify-gate protected).
