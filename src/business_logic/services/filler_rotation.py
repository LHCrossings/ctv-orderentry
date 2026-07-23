"""Korean-filler (K-FILLER) rotation for the Daily Programming filler auto-fill.

Korean dramas are 3 physical pieces (A/B/C); their program hour has more PRGS
slots than that (typically 5), so the leftover blank slots are padded with
K-FILLER spots. This module draws those fillers randomly WITHOUT replacement so
every active filler airs once before any repeats, then the cycle resets. State
lives in `chat.kfiller_rotation` (one row per filler code used this cycle); see
scripts/setup_kfiller_rotation_table.py.

The active pool = numbered K-FILLERs (`K-FILLER<yy>-NNN`, e.g. K-FILLER25-027),
excluding DO NOT USE / HIATUS name markers and expired spots. A single filler may
air across multiple markets, so a draw is shared network-wide (not per market).

Draw and mark are separate: `draw_n()` returns picks without recording them (so a
reroll is free); the caller records them with `mark_used()` only when the operator
commits the choice. Keyed on COD_PROGRA.
"""
from __future__ import annotations

import random

_ACTIVE_SQL = """
    SELECT ID_FILMATI, COD_PROGRA, DURATA
    FROM FILMATI WITH(NOLOCK)
    WHERE NEWTYPE = 'PGM'
      AND COD_PROGRA LIKE 'K-FILLER[0-9][0-9]-%'
      AND COD_PROGRA NOT LIKE '%DO NOT USE%' AND DESCRIZIO NOT LIKE '%DO NOT USE%'
      AND COD_PROGRA NOT LIKE '%HIATUS%'     AND DESCRIZIO NOT LIKE '%HIATUS%'
      AND (DATA_SCAD IS NULL OR DATA_SCAD >= CAST(GETDATE() AS DATE))
    ORDER BY COD_PROGRA
"""


def active_pool(cur) -> list[dict]:
    """All currently-usable numbered K-FILLERs: [{fid, code, durata}]."""
    cur.execute(_ACTIVE_SQL)
    return [{"fid": int(r[0]), "code": (r[1] or "").strip(), "durata": int(r[2] or 0)}
            for r in cur.fetchall()]


def _used(cur) -> set[str]:
    cur.execute("SELECT kf_code FROM chat.kfiller_rotation")
    return {(r[0] or "").strip() for r in cur.fetchall()}


def status(cur) -> dict:
    """Rotation status for the UI: fillers used vs. total active this cycle."""
    pool = active_pool(cur)
    used = _used(cur)
    codes = {p["code"] for p in pool}
    return {"total": len(codes), "used": len(codes & used), "remaining": len(codes - used)}


def draw_n(conn, n: int) -> list[dict]:
    """Return up to `n` DISTINCT active K-FILLERs not yet used this cycle, WITHOUT
    marking them used. If fewer than `n` remain unused, the cycle resets (the table
    is cleared) and the remainder is drawn from the full pool — so a single draw
    never repeats a filler within itself. Marking is deferred to mark_used()."""
    n = max(0, int(n))
    if not n:
        return []
    cur = conn.cursor()
    pool = active_pool(cur)
    if not pool:
        return []
    used = _used(cur)
    unused = [p for p in pool if p["code"] not in used]
    random.shuffle(unused)
    picks: list[dict] = []
    while len(picks) < n:
        if not unused:
            # Cycle exhausted mid-draw → start a fresh cycle, excluding what this
            # draw already took so the same filler isn't picked twice.
            cur.execute("DELETE FROM chat.kfiller_rotation")
            conn.commit()
            taken = {p["code"] for p in picks}
            unused = [p for p in pool if p["code"] not in taken]
            random.shuffle(unused)
            if not unused:
                break  # pool smaller than n — return what we have
        picks.append(unused.pop())
    return picks


_OVERSHOOT_CAP_FRAMES = int(5 * 60 * 29.97)  # allow up to ~5 min of overfill


def draw_until(cur, target_frames: int, exclude_codes=()) -> list[dict]:
    """Pick random DISTINCT active K-FILLERs whose durations fill `target_frames`
    — for WEEKEND programming-time fill. Pure random from the full active pool: it
    deliberately does NOT read or update the rotation cycle (weekend fillers get
    duration-match flexibility and don't consume weekday tokens).

    Biased to OVERFILL, not underfill: it always reaches the target and overshoots
    by up to ~5 minutes (only exceeding that if the pool leaves no smaller option).
    A spare filler is a one-click delete for master control, whereas underfilling
    means hand-inserting a filler across every market. Returns [] if target ≤ 0."""
    target = int(target_frames)
    if target <= 0:
        return []
    ex = {(c or "").strip() for c in exclude_codes}
    pool = [p for p in active_pool(cur) if p["code"] not in ex and p["durata"] > 0]
    random.shuffle(pool)
    picks, total = [], 0
    while total < target and pool:
        gap = target - total
        # A filler that completes the fill landing in [target, target+5min].
        finishers = [p for p in pool if gap <= p["durata"] <= gap + _OVERSHOOT_CAP_FRAMES]
        unders = [p for p in pool if p["durata"] < gap]
        if finishers:
            choice = random.choice(finishers)          # done, overshoot ≤ 5 min
        elif unders:
            choice = random.choice(unders)             # still short → add and continue
        else:
            choice = min(pool, key=lambda p: p["durata"])  # unavoidable → least overshoot
        pool.remove(choice)
        picks.append(choice)
        total += choice["durata"]
    return picks


def mark_used(conn, codes, used_by: str | None = None) -> None:
    """Record filler codes as used in the current cycle (idempotent per code)."""
    cur = conn.cursor()
    for code in {(c or "").strip() for c in codes if c}:
        cur.execute(
            "INSERT INTO chat.kfiller_rotation (kf_code, used_by) "
            "SELECT %s, %s WHERE NOT EXISTS (SELECT 1 FROM chat.kfiller_rotation WHERE kf_code=%s)",
            (code, used_by, code),
        )
    conn.commit()
