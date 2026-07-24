# Weekend Korean-Drama Programming — Saturday/Sunday rework

## Request (from master control, via Lee — 2026-07-24)
- **Saturday:** default = drama pieces only (3 dramas × A/B/C = 9 pieces), **no auto fillers**.
  Operator MAY add fillers manually; manual fillers land after piece C of drama 3
  (i.e. stacked behind the last piece — the existing float position).
- **Sunday (new 9-slot structure, effective 8/2; 7/26 handled manually by MC):**
  2 dramas (Thu/Fri) fill slots 1-6, then **one K-FILLER per open slot** (slots 7-9).
  Fillers use the existing random/no-token logic, and the 3 chosen fillers should make
  **drama + fillers as close to the programming budget as possible** (best-effort).

## Unified rule (drives Sat, Sun, and adapts to the live Etere slot count)
- Drama count = weekday-repeat pattern (Sat=3 Mon/Tue/Wed, Sun=2 Thu/Fri) — NOT `slots÷3`.
- Pieces fill the leading slots; open slots = total_slots − pieces.
- Sunday auto-draws one filler per open slot (chosen to best-match budget).
- Saturday has 0 open slots → 0 auto fillers; manual adds overflow-stack behind the last piece.
- No hardcoded date: 6-slot Sunday → 0 open → 0 auto fillers (7/26, MC-manual); 9-slot → 3.

## Changes
- [ ] `filler_rotation.py`: add `draw_k_near_target(cur, k, target_frames, exclude_codes)`
      — pick exactly k DISTINCT active K-FILLERs minimizing |target − Σdurata|, random
      (no rotation token), so rerolls vary. Best-effort; returns <=k if pool too small.
- [ ] `daily_programming_run.py::_place_weekend_drama_once`: relax `pieces == slots`;
      place content (pieces then fillers) one-per-slot into `slots`; overflow beyond
      the last slot stacks behind it (preserves Saturday manual-add + old behavior).
- [ ] `orders.py` `kdrama/weekend`: drama_count from `_kd_prefill_weekdays` length;
      return `openSlots` (= slots − pieces). Only build that many drama selectors.
- [ ] `orders.py` `kdrama/weekend/fillers`: if openSlots>0 -> `draw_k_near_target(openSlots,
      budget − drama_fr)`; else [] (Saturday). Report budget/drama/filler totals as today.
- [ ] `daily_programming.html` modal: Saturday (openSlots==0) -> no auto-draw, show a
      manual "add filler" affordance; Sunday (openSlots>0) -> auto-draw open-slot fillers
      + reroll. Run posts the chosen fillerIds as today.

## Verify (read-only — no live schedule writes)
- [ ] Query Etere 8/2 (Sun) Korean block: confirm 9 PRGS slots; compute pieces=6, open=3.
- [ ] Confirm `draw_k_near_target(3, budget−drama)` lands total near budget; reroll varies.
- [ ] Confirm Saturday (8/1) -> 9 slots, 9 pieces, 0 open, 0 auto fillers.
- [ ] Do NOT run the live weekend placement as a test (it writes the real schedule).

## Review (2026-07-24)
Implemented across 4 files (all compile; backend verified read-only against live Etere):
- `filler_rotation.draw_k_near_target` — k distinct fillers minimizing |budget − Σdur|,
  randomised (k-1 random + greedy completion, sampled). Verified: hit budget within ±1
  frame and varied between draws. No rotation token (like draw_until).
- `_place_weekend_drama_once` — content = pieces + fillers one-per-slot; overflow stacks
  behind the last slot. Relaxed the old `pieces == slots` hard requirement.
- `kdrama/weekend` — drama_count = len(weekday-repeats) (fixes the 9-slot Sunday showing
  3 selectors); returns openSlots.
- `kdrama/weekend/fillers` — Sunday draws one near-budget filler per open slot; Saturday [].
- `daily_programming.html` — Saturday: no auto fillers + ➕ Add filler (manual, removable);
  Sunday: auto-draw open-slot fillers + 🎲 reroll; dynamic hint text.

Live read-only verify: 8/1 & 8/2 both have 9 PRGS slots (08:00-10:00). picker ±1 frame.
Could not exercise piece-resolution end-to-end (that week's weekday KDramas not yet in
Etere) — counts derive from the weekday pattern regardless. Did NOT run live placement.

Effective: Sunday new-structure is 8/2+; 7/26 set up manually by MC (0-open-slot day →
0 auto fillers, as intended).
