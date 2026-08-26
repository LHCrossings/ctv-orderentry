"""Program-filler pools for the Daily Programming filler auto-fill.

Some shows don't fill their guide window — leftover PRGS slots (or leftover
time) are padded with language-appropriate FILLER assets. The pools, all
NEWTYPE='PGM' assets matched on COD_PROGRA:

  korean   → K-FILLER<yy>-NNN (e.g. K-FILLER25-027)
  chinese  → CHINESEFILLER… / UNIAM…
  filipino → UNIAE… (Filipino AND Jus Punjabi programming)

The Korean weekday auto-fill additionally rotates WITHOUT replacement so every
active K-FILLER airs once before any repeats, then the cycle resets. Rotation
state lives in `chat.kfiller_rotation` (one row per filler code used this
cycle; see scripts/setup_kfiller_rotation_table.py) and applies to the K pool
ONLY — duration-targeted draws (`draw_until`, `draw_k_near_target`) never read
or consume it, whatever the pool.

Draw and mark are separate: `draw_n()` returns picks without recording them (so a
reroll is free); the caller records them with `mark_used()` only when the operator
commits the choice. Keyed on COD_PROGRA.
"""

from __future__ import annotations

import random

K_POOL = ("K-FILLER[0-9][0-9]-%",)

# Auto-fill pool per language key. Values are COD_PROGRA LIKE patterns.
POOL_PATTERNS = {
    "korean": K_POOL,
    "chinese": ("CHINESEFILLER%", "UNIAM%"),
    "filipino": ("UNIAE%",),
}

# Grid-language word (programming_grid._parse_title, free text) → pool key.
_LANGUAGE_POOL = {
    "korean": "korean",
    "chinese": "chinese",
    "mandarin": "chinese",
    "cantonese": "chinese",
    "filipino": "filipino",
    "tagalog": "filipino",
    "punjabi": "filipino",
}

# Codes that are fillers, not show pieces — excluded from placed-group anchor
# computation (_is_placed in daily_programming_run.py): letterless filler codes
# self-anchor, and one stacked near a window's end would mark the NEXT window
# "already placed". MIRRORS isFillerCode() in daily_programming.html — keep in sync.
FILLER_CODE_PREFIXES = ("K-FILLER", "CHINESEFILLER", "UNIAE", "UNIAM")


def pool_for_language(language) -> str | None:
    """POOL_PATTERNS key for a grid language word, or None (manual search only)."""
    return _LANGUAGE_POOL.get((language or "").strip().lower())


_ACTIVE_SQL = """
    SELECT ID_FILMATI, COD_PROGRA, DURATA
    FROM FILMATI WITH(NOLOCK)
    WHERE NEWTYPE = 'PGM'
      AND ({patterns})
      AND COD_PROGRA NOT LIKE '%DO NOT USE%' AND DESCRIZIO NOT LIKE '%DO NOT USE%'
      AND COD_PROGRA NOT LIKE '%HIATUS%'     AND DESCRIZIO NOT LIKE '%HIATUS%'
      AND (DATA_SCAD IS NULL OR DATA_SCAD >= CAST(GETDATE() AS DATE))
    ORDER BY COD_PROGRA
"""


def active_pool(cur, patterns=K_POOL) -> list[dict]:
    """All currently-usable fillers matching `patterns`: [{fid, code, durata}]."""
    sql = _ACTIVE_SQL.format(patterns=" OR ".join("COD_PROGRA LIKE %s" for _ in patterns))
    cur.execute(sql, tuple(patterns))
    return [
        {"fid": int(r[0]), "code": (r[1] or "").strip(), "durata": int(r[2] or 0)}
        for r in cur.fetchall()
    ]


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


def draw_until(cur, target_frames: int, exclude_codes=(), patterns=K_POOL) -> list[dict]:
    """Pick random DISTINCT active fillers from `patterns` whose durations fill
    `target_frames` — for programming-time fill (weekend K-drama, and the setup
    flow's "Auto-fill leftover"). Pure random from the full active pool: it
    deliberately does NOT read or update the rotation cycle (duration-targeted
    draws get duration-match flexibility and never consume weekday tokens).

    Biased to OVERFILL, not underfill: it always reaches the target and overshoots
    by up to ~5 minutes (only exceeding that if the pool leaves no smaller option).
    A spare filler is a one-click delete for master control, whereas underfilling
    means hand-inserting a filler across every market. Returns [] if target ≤ 0."""
    target = int(target_frames)
    if target <= 0:
        return []
    ex = {(c or "").strip() for c in exclude_codes}
    pool = [p for p in active_pool(cur, patterns) if p["code"] not in ex and p["durata"] > 0]
    random.shuffle(pool)
    picks, total = [], 0
    while total < target and pool:
        gap = target - total
        # A filler that completes the fill landing in [target, target+5min].
        finishers = [p for p in pool if gap <= p["durata"] <= gap + _OVERSHOOT_CAP_FRAMES]
        unders = [p for p in pool if p["durata"] < gap]
        if finishers:
            choice = random.choice(finishers)  # done, overshoot ≤ 5 min
        elif unders:
            choice = random.choice(unders)  # still short → add and continue
        else:
            choice = min(pool, key=lambda p: p["durata"])  # unavoidable → least overshoot
        pool.remove(choice)
        picks.append(choice)
        total += choice["durata"]
    return picks


def draw_k_near_target(cur, k, target_frames, exclude_codes=(), _samples=4000) -> list[dict]:
    """Pick EXACTLY k distinct active K-FILLERs (random, no rotation token) whose
    combined duration makes the running programme total land as close to
    `target_frames` as possible — for the weekend open-slot fill, where the slot
    COUNT is fixed (one filler per open PRGS slot) but we still want drama+fillers
    to sit near the programming budget.

    Best-effort and randomised: each sample fixes k-1 fillers at random, then
    greedily completes with the pool filler whose duration best closes the gap;
    the closest sample across `_samples` tries wins. A reroll yields a different
    near-optimal set. Returns fewer than k only if the active pool is smaller
    than k. Like draw_until(), it does NOT read or update the rotation cycle."""
    k = max(0, int(k))
    if not k:
        return []
    ex = {(c or "").strip() for c in exclude_codes}
    pool = [p for p in active_pool(cur) if p["durata"] > 0 and p["code"] not in ex]
    if len(pool) <= k:
        return pool  # not enough to choose from — take whatever exists
    target = int(target_frames)
    if target <= 0:
        random.shuffle(pool)
        return pool[:k]
    best, best_err = None, None
    for _ in range(int(_samples)):
        base = random.sample(pool, k - 1) if k > 1 else []
        gap = target - sum(p["durata"] for p in base)
        base_codes = {p["code"] for p in base}
        rest = [p for p in pool if p["code"] not in base_codes]
        last = min(rest, key=lambda p: abs(gap - p["durata"]))
        cand = base + [last]
        err = abs(target - sum(p["durata"] for p in cand))
        if best_err is None or err < best_err:
            best, best_err = cand, err
            if err == 0:
                break
    random.shuffle(best)  # slot order shouldn't track duration
    return best


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
