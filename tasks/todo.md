# Break Optimization — "Programming Not Placed" guard (Jenna, 2026-08-12)

Day-of EE compaction scrunches later shows' spots into earlier positions when
programming hasn't been inserted yet. BO then sees one giant break, flags phantom
Separation/Out-of-Order violations, and — worse — Apply Fix would physically repack
spots that belong to other programming. Goal: detect, inform richly, and make the
write impossible.

Detection signals (verified live 2026-08-12):
- `trafficTPalinse.Offset` (= trafficPalinse.offset) holds each contract spot's
  intended nominal break start; survives both BO packing and EE day-of scrunch.
- A window with zero live PGM rows = show not placed.
- A spot whose Offset >= show window end = absorbed from later programming.

## Tasks
- [x] orders.py: hoist `_BO_FPS` / `_bo_frames_to_time` to module level
- [x] orders.py: add `tp.Offset` to the BO fetch; carry `intended_ora`/`intended_time` per spot
- [x] orders.py: extract segmentation into module-level `_bo_build_breaks(annotated, to_frames)`
      returning `(breaks, window_has_pgm)`; flag breaks `programming_missing` when
      (a) window has no PGM rows (NOOP ≠ programming), or (b) break contains spots with
      Offset >= window end (reason "window" / "absorbed")
- [x] orders.py: flagged breaks → optimized = identity (new_ora = current ora, changed=False,
      no violations) so /apply and /bulk-apply can never move them; skipped in
      `_bo_resolve_pi_duplicates` (no creative swaps) and `_bo_check_separation`
- [x] orders.py: explicit `not b.get("programming_missing")` guard in bulk-apply's
      changed_breaks + `breaks_waiting` in bulk results
- [x] orders.py: envelope gains `programming_placed`
- [x] both templates (in sync): badge, dashed nord9 card, pm-panel with reason + absorbed-spot
      list ("belongs in the H:MM break"), inline "→ H:MM" chip on foreign rows, Apply Fix
      removed, "waiting on programming" summary count, show-header stat + empty-state on log page
- [x] tests: 10 new unit tests on `_bo_build_breaks`
- [x] full unit suite (522 passed); JS of both templates node --check clean
- [x] live end-to-end via TestClient against today's DB: NYC 10–11a Break 6 flagged
      "absorbed" (RWNYC30V01 scrunched up from its intended 11:18 break), inert; breaks 1–5
      still fully checked

## Review
Root cause confirmed: BO's break segmentation is purely "runs of ad rows split on PGM
rows in XORDER order" — a window whose programming isn't inserted yet has no delimiters,
so EE's day-of compaction turns it into one phantom mega-break with artifact
separation/order violations, and Apply Fix would have written the repack (the 7/10
Korean News corruption pattern). Detection uses two positive signals: no live PGM row in
the window, and trafficPalinse.offset (intended nominal break position, verified live to
survive both BO packing and the EE scrunch) at/after the window end. Flagged breaks are
inert end-to-end (identity optimization + client and server guards) and maximally
informational (which spots were absorbed, where they actually belong).
