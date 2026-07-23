"""Deterministic placement engine for the Set up Daily Programming tool.

Places a program into the PRGS (program) segments of one market's schedule,
mirroring what Etere's drag + Explode does, but deterministically and
transaction-safe. Two paths:

  * explode  – one EDL-marked file split into N segments (dbo.ExplodeEdl plan),
               grouped under Part-1's Event_P, timecodes carved per segment.
  * pieces   – N standalone whole-file pieces (a/b/c…) + fillers, each its own
               event, placed one per PRGS slot in order (shows first, fillers next).
  * shoplc   – fixed overnight Shop LC setup (SHOP_LC map): CVC/SFO get their
               ID-overlay live asset exploded per EDL, every other CTV market gets
               the generic live asset as one unexploded 6-hour event. First piece
               is always the fixed-time (F) anchor — a live feed joins at 00:00
               sharp. Profiles and bumper logic do not apply.

Both honour the open/close bumper rules and the slot/break count. Every market
is committed only if it passes the verify gate, else rolled back untouched.

See tasks/daily_programming_discovery.md + the project memory for the recipe.
"""
from __future__ import annotations

import random
import re
import threading
import time

from src.business_logic.services.show_profiles import daily_elements, elements_for, profile_for

FPS = 29.97
EVENT_BASE = 100000000000  # Event_P = EVENT_BASE + id_tpalinse


DAY_FRAMES = int(24 * 3600 * FPS)  # one broadcast-day's worth of frames

# Per-asset locks guarding the FILMATI read-then-write in _apply_filmati_sync.
# Market threads run in parallel on separate connections; a shared asset (one
# show file exploded into every market, a shared bumper, a shared FCC-ID
# element) is the SAME FILMATI row for all of them. Two sessions each holding
# a SELECT's shared lock on that row and then both trying to UPDATE it is a
# textbook SQL Server 1205 deadlock (lock-conversion cycle). Serializing the
# read-then-write in Python turns that race into a plain, safe wait instead.
# A thread never holds one fid's lock while waiting on another's (acquire is
# always non-blocking, release always precedes any further wait), so a cycle
# among these locks can't form. See _sync_checksums / _drain_pending_filmati_syncs.
_FILMATI_LOCKS = {}
_FILMATI_LOCKS_GUARD = threading.Lock()
_FILMATI_RETRY_SECONDS = 2

# SQL Server 1205 (deadlock victim) can still happen even with the FILMATI
# lock above — that lock only covers contention among OUR OWN market threads.
# Etere's own Aligner independently reads/writes FILMATI (see _sync_checksums'
# docstring) on its own schedule, outside this process entirely; we can't lock
# against an actor we don't share memory with. SQL Server's own error text
# says to rerun the transaction, so run_market() does exactly that — a bounded
# retry, not a preemptive lock, since we can't identify every possible party.
# Retry delays carry random jitter: on the 2026-07-10 Korean News run, four
# market threads deadlocked each other, all slept exactly 1s, and re-collided
# on every retry until all four exhausted their attempts. Desynchronized
# sleeps break that lockstep.
_DEADLOCK_MAX_ATTEMPTS = 5
_DEADLOCK_RETRY_SECONDS = 1

# sch_rebuildStartTimeSchedule is the most lock-hungry statement in a placement
# (it rescans and rewrites TPALINSE start times for the whole day). Concurrent
# rebuilds from different market threads are the likeliest 1205 parties, so run
# only one at a time process-wide; each market's data is disjoint, so ordering
# between them doesn't matter.
_REBUILD_LOCK = threading.Lock()


def _is_deadlock(exc):
    """True for SQL Server error 1205. pymssql surfaces DB-Lib errors as an
    exception whose first arg is the numeric error code, e.g.
    args == (1205, b"...deadlock victim...")."""
    args = getattr(exc, "args", None)
    return bool(args) and args[0] == 1205


def _filmati_lock(fid):
    with _FILMATI_LOCKS_GUARD:
        lock = _FILMATI_LOCKS.get(fid)
        if lock is None:
            lock = _FILMATI_LOCKS[fid] = threading.Lock()
        return lock

# Shop LC overnight (00:00–06:00) — static setup, CTV markets only, never DAL.
# All three assets are live events (FILMATI.LIVE_ID='1'); _sync_checksums leaves
# live assets' FILMATI fields untouched.
SHOP_LC = {
    "window": ("00:00", "06:00"),
    "explode": {4: 2152, 7: 2810},  # SFO, CVC — EDL-sliced hourly (station-ID overlay)
    "generic": 2811,                # every other CTV market — single 6h event
}


def _frames(hhmm: str):
    """Frame-of-day for a broadcast-day time.

    The broadcast day runs 06:00 → 30:00 (i.e. 06:00 today through 05:59 the next
    morning), and Etere stores block/segment offsets and TPALINSE.ORA on that scale:
    06:00 = 6h, and the post-midnight tail 00:00–05:59 lives at 24:00–29:59 on the
    SAME date. So a grid time whose hour is < 6 belongs to that tail and must be
    shifted by +24h; otherwise the window would land at 0–6h where nothing exists
    (the "0 breaks / too many pieces" and silent no-placement bugs)."""
    m = re.match(r"\s*(\d{1,2}):(\d{2})", hhmm or "")
    if not m:
        return None
    h, mins = int(m.group(1)), int(m.group(2))
    if h < 6:  # post-midnight tail of the broadcast day
        h += 24
    return int((h * 3600 + mins * 60) * FPS)


def _window(start: str, end: str):
    """(lo, hi) broadcast-day frame window for a grid block. Handles the day's final
    block, which ends at 06:00 the next morning (30:00): after the <6h shift its end
    can still wrap below its start, so bump it a further 24h."""
    lo, hi = _frames(start), _frames(end)
    if lo is not None and hi is not None and hi <= lo:
        hi += DAY_FRAMES
    return lo, hi


def _slots(cur, cod_user, d, lo, hi, seg_type="PRGS"):
    """Segments of `seg_type` ('PRGS' program breaks or 'COMS' commercial breaks)
    in the window, ordered by start time: list of dicts."""
    cur.execute(
        """SELECT sb.ID_TrafficSchedule sch, bl.ID_TrafficBlock blk, seg.ID_TrafficSegment seg,
                  (sb.offset+seg.Offset) st
           FROM traffic_calendar ca WITH(NOLOCK)
           JOIN traffic_scheduleblock sb WITH(NOLOCK) ON sb.ID_TrafficSchedule=ca.ID_TrafficSchedule
           JOIN traffic_block bl WITH(NOLOCK) ON bl.ID_TrafficBlock=sb.ID_TrafficBlock
           OUTER APPLY (
             SELECT si.ID_TrafficSegment,si.Offset,si.Type FROM trf_instancesegment si WITH(NOLOCK)
               WHERE si.ID_TrafficBlock=bl.ID_TrafficBlock AND si.COD_USER=ca.Cod_User AND si.INSTANCEDATE=ca.Date AND si.visible=1
             UNION
             SELECT se.ID_TrafficSegment,se.Offset,se.Type FROM traffic_segment se WITH(NOLOCK)
               WHERE se.ID_TrafficBlock=bl.ID_TrafficBlock AND se.visible=1
               AND (SELECT COUNT(*) FROM trf_instancesegment WITH(NOLOCK) WHERE ID_TrafficBlock=bl.ID_TrafficBlock AND COD_USER=ca.Cod_User AND INSTANCEDATE=ca.Date)=0
           ) seg
           WHERE ca.Cod_User=%s AND ca.Date=%s AND bl.expired=0 AND seg.Type=%s
             AND (sb.offset+seg.Offset)>=%s AND (sb.offset+seg.Offset)<%s
           ORDER BY st""",
        (cod_user, d, seg_type, lo, hi),
    )
    return [{"sched": r[0], "block": r[1], "seg": r[2], "ora": int(r[3])} for r in cur.fetchall()]


def _explode_plan(cur, filmati, cod_user):
    cur.execute(
        "SELECT MARKIN, MARKOUT FROM dbo.ExplodeEdl(%s,0,N'eeAutomatic',%s,dbo.sch_GetInfDigit(%s,%s))",
        (filmati, cod_user, filmati, cod_user),
    )
    return [(int(a), int(b)) for a, b in cur.fetchall()]


def _bumpers(cur, cod_user, d, lo, hi):
    """Return (open, close) bumper dicts {id, xorder} in the window, or None each."""
    cur.execute(
        """SELECT id_tpalinse, XORDER, COD_PROGRA FROM TPALINSE
           WHERE COD_USER=%s AND DATA=%s AND ORA>=%s AND ORA<%s AND LIVELLO=0
             AND COD_PROGRA LIKE 'BUMP%%'""",
        (cod_user, d, lo, hi),
    )
    op = cl = None
    for idt, xo, code in cur.fetchall():
        u = (code or "").upper()
        if "OPEN" in u:
            op = {"id": idt, "xorder": int(xo)}
        elif "CLOS" in u:
            cl = {"id": idt, "xorder": int(xo)}
    return op, cl


def _clear_noop_fillers(cur, cod_user, d, lo, hi):
    """Soft-delete Etere's NOOP gap-filler events overlapping the window.

    When a program hole sits unfilled, Etere's playlist generation drops a
    NEWTYPE='NOOP' filler event spanning the hole (~50 min for a news hour).
    Placing program parts around a live NOOP corrupts the start-time rebuild —
    the parts land overlapping each other and verify fails ("Korean News · NYC",
    2026-07-10: every market whose 8:10a NOOP was still active failed, every
    market without one placed cleanly). Etere itself soft-deletes consumed
    NOOPs to LIVELLO=666, so mirror that. Runs inside the placement
    transaction — a verify failure rolls the NOOPs back to active."""
    cur.execute(
        """UPDATE TPALINSE SET LIVELLO=666
           WHERE COD_USER=%s AND DATA=%s AND NEWTYPE='NOOP' AND LIVELLO=0
             AND ORA < %s AND ORA + DURATION > %s""",
        (cod_user, d, hi, lo),
    )
    return cur.rowcount


_PIECE_GROUP_GAP = int(3 * 3600 * FPS)  # same-base rows further apart = a repeat airing


def _piece_base(code):
    """Show base of a piece code: NAMASTE071826E → NAMASTE071826 (pieces of one
    show share the base and differ only in the trailing letter)."""
    c = (code or "").strip()
    return c[:-1] if c[-1:].isalpha() and c[-1:].isupper() else c


def _group_anchors(rows):
    """Anchor ORA of each placed program group. rows = (ORA, COD_PROGRA).
    Same-base rows minutes apart are one show's pieces/parts; a gap over
    _PIECE_GROUP_GAP starts a new group (a repeat airing of the same file).
    The group's first ORA is where the show starts — that anchor, not each
    row's own ORA, decides which show window the group belongs to. Long
    breaks can push a show's last piece past the next window's start
    (SFO/CVC 2026-07-18: Namaste piece E at 15:02 inside the India Waves
    window made IW look placed while it was empty)."""
    last = {}   # base -> (ora of the base's previous row)
    anchors = []
    for ora, code in sorted(rows):
        base = _piece_base(code)
        prev = last.get(base)
        if prev is None or ora - prev > _PIECE_GROUP_GAP:
            anchors.append(ora)
        last[base] = ora
    return anchors


_ANCHOR_TOL = int(2 * 60 * FPS)  # a show's piece A chains in ~1s BEFORE its nominal window


def _is_placed(cur, cod_user, d, lo, hi):
    """True if a program GROUP is anchored inside [lo-tol, hi-tol) — the
    window's own show is placed. Counting raw rows in the window would claim
    the previous show's drifted last piece as this window's content, and an
    un-shifted anchor test would miss the window's own piece A (it starts as
    the previous hour's tail ends, ~1s before the nominal boundary) while
    catching the NEXT show's piece A at hi-1s."""
    cur.execute(
        """SELECT ORA, COD_PROGRA FROM TPALINSE WHERE COD_USER=%s AND DATA=%s
           AND NEWTYPE='PGM' AND LIVELLO=0 AND ID_FILMATI>0 AND COD_PROGRA NOT LIKE 'BUMP%%'""",
        (cod_user, d),
    )
    rows = [(int(r[0]), r[1] or "") for r in cur.fetchall()]
    return any(lo - _ANCHOR_TOL <= a < hi - _ANCHOR_TOL for a in _group_anchors(rows))


def _durata(cur, filmati):
    cur.execute("SELECT DURATA FROM FILMATI WHERE ID_FILMATI=%s", (filmati,))
    r = cur.fetchone()
    return int(r[0]) if r and r[0] else 0


def _insert_event(cur, cod_user, d, sched, block, seg, ora, filmati, duration):
    """Traffic_InsertEvent into a block+segment; return the new TPALINSE id (found by ora)."""
    cur.execute(
        f"EXEC Traffic_InsertEvent 0,'{d}',{cod_user},{sched},{block},{seg},{ora},'{d}',{ora},'{d}',0,{filmati},0,0,0,0,0,0,0,{duration}"
    )
    try:
        while cur.nextset():
            pass
    except Exception:
        pass
    cur.execute(
        "SELECT id_tpalinse FROM TPALINSE WHERE COD_USER=%s AND DATA=%s AND ID_FILMATI=%s AND ORA=%s AND PART=0",
        (cod_user, d, filmati, ora),
    )
    return cur.fetchone()[0]


def _ensure_after(cur, cod_user, d, row_id, before_xorder):
    """Make row_id sort AFTER before_xorder (e.g. Part1 after open bumper)."""
    cur.execute("SELECT XORDER FROM TPALINSE WHERE id_tpalinse=%s", (row_id,))
    if cur.fetchone()[0] > before_xorder:
        return
    cur.execute(
        "SELECT MIN(XORDER) FROM TPALINSE WHERE COD_USER=%s AND DATA=%s AND XORDER>%s AND id_tpalinse<>%s",
        (cod_user, d, before_xorder, row_id),
    )
    nxt = cur.fetchone()[0]
    newx = (before_xorder + nxt) // 2 if nxt else before_xorder + 1000
    cur.execute("UPDATE TPALINSE SET XORDER=%s WHERE id_tpalinse=%s", (newx, row_id))


def _close_guarantee(cur, cod_user, d, last_id, close_b):
    """Ensure the close bumper sorts AFTER the last program element."""
    if not close_b:
        return
    cur.execute("SELECT XORDER FROM TPALINSE WHERE id_tpalinse=%s", (last_id,))
    last_x = cur.fetchone()[0]
    if close_b["xorder"] > last_x:
        return
    cur.execute(
        "SELECT MIN(XORDER) FROM TPALINSE WHERE COD_USER=%s AND DATA=%s AND XORDER>%s AND id_tpalinse<>%s",
        (cod_user, d, last_x, close_b["id"]),
    )
    nxt = cur.fetchone()[0]
    newx = (last_x + nxt) // 2 if nxt else last_x + 5000
    cur.execute("UPDATE TPALINSE SET XORDER=%s WHERE id_tpalinse=%s", (newx, close_b["id"]))


def _ensure_bumper(cur, cod_user, d, slot, spec, existing):
    """Ensure a required bumper exists. If one is already in the window (`existing`,
    from _bumpers) return it. Otherwise insert the bumper file (resolved by code)
    into `slot` and return the new {id, xorder}. Type/order conform is done by the
    caller. Returns None only if the bumper file can't be found in the library."""
    if existing:
        return existing
    cur.execute("SELECT ID_FILMATI, DURATA FROM FILMATI WHERE COD_PROGRA=%s", (spec["code"],))
    r = cur.fetchone()
    if not r:
        return None
    fid, dur = int(r[0]), int(r[1] or 0)
    nid = _insert_event(cur, cod_user, d, slot["sched"], slot["block"], slot["seg"],
                        slot["ora"], fid, dur)
    cur.execute("EXEC sch_UpdateSupportAndProperties %s,%s,1", (nid, fid))
    cur.execute("SELECT XORDER FROM TPALINSE WHERE id_tpalinse=%s", (nid,))
    return {"id": nid, "xorder": int(cur.fetchone()[0])}


def _apply_filmati_sync(cur, fid, fid_rows):
    """The actual read-then-write against one FILMATI row (normalize its
    checksum-input fields) plus the checksum freeze on each of its TPALINSE
    rows. Must only ever run under _filmati_lock(fid) — see callers."""
    cur.execute("SELECT LIVE_ID FROM FILMATI WHERE ID_FILMATI=%s", (fid,))
    r = cur.fetchone()
    is_live = bool(r and r[0] is not None)
    if not is_live:
        # Live event: LIVE_ID is the live-feed link and the asset never goes
        # through the Aligner — its fields as-is are the settled state.
        cur.execute("""
            UPDATE FILMATI SET
                INF_DIGIT=0,
                AUDIO=NULL,
                AUDIO_LANGUAGE=NULL,
                LIVE_ID=NULL
            WHERE ID_FILMATI=%s
        """, (fid,))

    for rid, prog_code in fid_rows:
        if is_live:
            # Live-asset row: sch_UpdateSupportAndProperties already wrote the
            # REAL supporto/CRAWL_DESC — the Explode-mimicking cosmetics below
            # would destroy both (root cause of the 2026-07-08 Shop LC dead-air
            # incident) — freeze the checksum only.
            cur.execute(
                "UPDATE TPALINSE SET SCHEDULE_CHECKSUM = dbo.sch_getFilmatiCheckSum(%s) WHERE id_tpalinse=%s",
                (rid, rid))
            continue

        supporto_val = f"0ETX      {prog_code}"
        crawl_desc = "[EDL]\nEdl_Version=0\n[Aspect Conversion]\nCode=HL"
        cur.execute("""
            UPDATE TPALINSE SET
                tipo_tc='C',
                aspect='H',
                audio_ty='M',
                supporto=%s,
                visionato='X',
                CRAWL_DESC=%s,
                SCHEDULE_CHECKSUM = dbo.sch_getFilmatiCheckSum(%s)
            WHERE id_tpalinse=%s
        """, (supporto_val, crawl_desc, rid, rid))


def _drain_pending_filmati_syncs(cur, conn, pending):
    """Retry any FILMATI syncs _sync_checksums deferred because another
    market's thread held the lock on that same asset. Called once a market
    thread has placed everything it was given, so there's nothing left to
    interleave with. Round-robins the remaining items so one stubborn fid
    can't starve the others; sleeps between full passes only if every item
    in that pass was still contended. Never skips — retries until each item
    lands, then commits it."""
    while pending:
        remaining = []
        progressed = False
        for fid, fid_rows in pending:
            lock = _filmati_lock(fid)
            if lock.acquire(blocking=False):
                try:
                    _apply_filmati_sync(cur, fid, fid_rows)
                    conn.commit()
                finally:
                    lock.release()
                progressed = True
                print(f"[daily-programming] FILMATI fid={fid} sync drained "
                      f"(rows={[r for r, _ in fid_rows]})")
            else:
                remaining.append((fid, fid_rows))
        pending = remaining
        if pending and not progressed:
            print(f"[daily-programming] FILMATI still contended for fids="
                  f"{[fid for fid, _ in pending]}, retrying in {_FILMATI_RETRY_SECONDS}s")
            time.sleep(_FILMATI_RETRY_SECONDS)


def _sync_checksums(cur, ids, pending):
    """Store each row's SCHEDULE_CHECKSUM so Etere never shows the yellow
    'event modified — needs Explode - all breakpoints' triangle.

    The triangle appears when the row's stored TPALINSE.SCHEDULE_CHECKSUM differs
    from the live value of dbo.sch_getFilmatiCheckSum(id_tpalinse). That function
    reads exactly six FILMATI columns (verified empirically 2026-07-06):
        POS_INI, POS_FIN, INF_DIGIT, AUDIO, AUDIO_LANGUAGE, LIVE_ID
    Nothing about the physical media bytes (DUR_FISICA, BITRATE, etc.) feeds it.

    Historically we synced the checksum at placement while the file was still on
    S3 (green triangle). Etere's Aligner then pulled the file down and normalised
    a couple of those input fields to their canonical settled state, which changed
    the live checksum — so our frozen copy went stale and the triangle stuck (and
    never cleared until an operator ran Explode-all-breakpoints post-download).

    The settled/canonical state of every FILE-BASED asset in the library is
    deterministic and fully known at placement time — INF_DIGIT=0 and
    AUDIO/AUDIO_LANGUAGE/LIVE_ID NULL (POS_INI/POS_FIN are the file's real trim,
    already correct from ingest/EDL import, so we leave them). So we force those
    input fields to canonical *before* computing the checksum: the value we store
    then equals what Etere computes after the download, and the triangle never
    appears — no dependency on Aligner timing.

    LIVE EVENTS ARE EXEMPT (2026-07-08). An asset with LIVE_ID set (e.g. Shop LC
    overnight feeds 2810/2152/2811) takes a live feed instead of a file — the
    Aligner never touches it, so its current field values already ARE the settled
    state. Nulling LIVE_ID here permanently severed the live-feed link on the
    FILMATI record itself (asset 2810 went dark for CVC), which is an asset-library
    mutation, not a schedule-row one. For live assets we skip the normalisation
    entirely and freeze the checksum against the fields as-is.

    NOTE: the tipo_tc/aspect/audio_ty/supporto/visionato/CRAWL_DESC fields set below
    are NOT inputs to the checksum — they are cosmetic (they mirror what Explode
    writes so the row looks identical in the UI). The FILMATI normalisation above is
    what actually prevents the triangle. Idempotent; also auto-heals stale rows.

    CONCURRENCY (2026-07-09): rows are grouped by their FILMATI id and each
    group's read-then-write runs under _filmati_lock(fid). Daily Programming
    runs one market per thread, and a shared asset (a show file exploded into
    every market, a shared bumper, a shared FCC-ID element) is the SAME
    FILMATI row across all of them — two threads racing a SELECT-then-UPDATE
    on that row is what produced the 1205 deadlock ("Frontline Pilipinas ·
    SFO", 2026-07-09). If another thread already holds that fid's lock, this
    group is appended to `pending` instead of blocking — the caller places
    the rest of its assignments first and retries pending items once it has
    nothing else to do (see _drain_pending_filmati_syncs).
    """
    rows = []
    for rid in ids:
        if rid is None:
            continue
        cur.execute("SELECT COD_PROGRA, ID_FILMATI FROM TPALINSE WHERE id_tpalinse=%s", (rid,))
        row = cur.fetchone()
        if row and row[1]:
            rows.append((rid, row[0] or "", row[1]))

    by_fid = {}
    for rid, prog_code, fid in rows:
        by_fid.setdefault(fid, []).append((rid, prog_code))

    for fid, fid_rows in by_fid.items():
        lock = _filmati_lock(fid)
        if lock.acquire(blocking=False):
            try:
                _apply_filmati_sync(cur, fid, fid_rows)
            finally:
                lock.release()
        else:
            print(f"[daily-programming] FILMATI fid={fid} locked by another "
                  f"market's thread — deferring sync for rows={[r for r, _ in fid_rows]}")
            pending.append((fid, fid_rows))


def _place_element(cur, cod_user, d, lo, hi, prgs_slots, el, program_first_id):
    """Place one profile element (e.g. an FCC ID) for this market, on top of the
    already-placed program.

    Resolves the asset (by `id`, else by `code`), finds the target break of the
    element's segment type ('PRGS' or 'COMS') at the 1-based `break` index (or
    "last"), inserts it, sets EVENT_TYPE, and positions it:
      * "first": if the element is the anchor it becomes the F-lock — the program's
        first piece is flipped to 'T' and ordered after the element.
      * "last": best-effort — left where it lands; the break optimizer carries IDs
        to final position.
    Returns {id, xorder} or None if the asset or target break can't be found.
    """
    fid = el.get("id")
    if not fid:
        cur.execute("SELECT ID_FILMATI FROM FILMATI WHERE COD_PROGRA=%s", (el.get("code"),))
        r = cur.fetchone()
        if not r:
            return None
        fid = r[0]
    fid = int(fid)

    seg_type = el.get("segment", "PRGS")
    seg_slots = prgs_slots if seg_type == "PRGS" else _slots(cur, cod_user, d, lo, hi, seg_type)
    brk = el.get("break", 1)
    if brk == "last":
        slot = seg_slots[-1] if seg_slots else None
    else:
        slot = seg_slots[brk - 1] if 0 < brk <= len(seg_slots) else None
    if slot is None:
        return None

    nid = _insert_event(cur, cod_user, d, slot["sched"], slot["block"], slot["seg"],
                        slot["ora"], fid, _durata(cur, fid))
    cur.execute("EXEC sch_UpdateSupportAndProperties %s,%s,1", (nid, fid))
    cur.execute("UPDATE TPalinse SET EVENT_TYPE=%s WHERE id_tpalinse=%s", (el.get("event_type", "T"), nid))
    cur.execute("SELECT XORDER FROM TPALINSE WHERE id_tpalinse=%s", (nid,))
    xo = int(cur.fetchone()[0])

    if el.get("position") == "first" and el.get("anchor") and program_first_id is not None:
        cur.execute("UPDATE TPalinse SET EVENT_TYPE='T' WHERE id_tpalinse=%s", (program_first_id,))
        _ensure_after(cur, cod_user, d, program_first_id, xo)
    return {"id": nid, "xorder": xo}


def _conform_window_xorder(cur, cod_user, d, lo, hi, part_ids, part_keys, open_b, close_b):
    """Reassign the window's XORDERs so the play order is the intended one:
    open bumper, part 1, break-1 spots, part 2, break-2 spots, …, last part,
    close bumper, final-break spots.

    Traffic_InsertEvent derives a new row's XORDER from its ORA-neighbors —
    including soft-deleted (LIVELLO=666) rows with STALE xorders, and spot rows
    whose ORA a break-optimization pass packed to the top of a program-less
    hour. On the 2026-07-10 Korean News run that gave parts 2–5 xorders
    interleaved wrongly with the commercial pod (two literally duplicated a
    dead NOOP's xorder), so the start-time rebuild chained the rows in a
    nonsense order and verify failed with overlapping parts.

    Spots carry their true break in trafficPalinse.offset (the BO pass moves
    TPALINSE.ORA but not that), so the intended order is fully recoverable:
    sort by (break offset — or current ORA for non-traffic rows, current ORA,
    current xorder), with parts keyed at their slot's nominal ora, the open
    bumper first, and the close bumper right after the last part. The SAME
    multiset of xorders the active window rows already hold is reassigned in
    that order, so relative order against everything outside the window is
    untouched."""
    cur.execute(
        """SELECT ID_TPALINSE, ORA, XORDER FROM TPALINSE
           WHERE COD_USER=%s AND DATA=%s AND ORA>=%s AND ORA<%s AND LIVELLO=0""",
        (cod_user, d, lo, hi),
    )
    rows = {r[0]: (int(r[1]), int(r[2])) for r in cur.fetchall()}
    if not rows:
        return
    ids_csv = ",".join(str(i) for i in rows)
    cur.execute(
        f"SELECT id_tpalinse, offset FROM trafficPalinse WHERE id_tpalinse IN ({ids_csv})"
    )
    tp_off = {r[0]: int(r[1]) for r in cur.fetchall()}

    keys = {rid: (tp_off.get(rid, ora), ora, xo) for rid, (ora, xo) in rows.items()}
    for rid, slot_ora in zip(part_ids, part_keys):
        if rid in keys:
            keys[rid] = (slot_ora, 0, 0)
    if open_b and open_b["id"] in keys:
        keys[open_b["id"]] = (lo - 1, 0, 0)
    if close_b and close_b["id"] in keys:
        keys[close_b["id"]] = (max(part_keys) + 1, 0, 0)

    order = sorted(rows, key=lambda rid: keys[rid])
    xorders = sorted(xo for _, xo in rows.values())
    for rid, xo in zip(order, xorders):
        if rows[rid][1] != xo:
            cur.execute("UPDATE TPALINSE SET XORDER=%s WHERE id_tpalinse=%s", (xo, rid))
    # keep the in-memory bumper xorders current for the element/close steps
    for b in (open_b, close_b):
        if b and b["id"] in rows:
            b["xorder"] = xorders[order.index(b["id"])]


def _rebuild(cur, d, cod_user, fromid):
    with _REBUILD_LOCK:
        cur.execute("EXEC dbo.sch_rebuildStartTimeSchedule %s,%s,0,0,NULL,%s,-1,0,1", (d, cod_user, fromid))
        try:
            while cur.nextset():
                pass
        except Exception:
            pass


def _verify_sequence(cur, ids, open_b, close_b):
    """ids = inserted program rows in slot order. Check: open bumper (if any) before
    the first, rows strictly non-overlapping, close bumper (if any) after the last."""
    cur.execute(
        "SELECT id_tpalinse, ORA, DURATION FROM TPALINSE WHERE id_tpalinse IN (%s)"
        % ",".join(str(i) for i in ids)
    )
    by_id = {r[0]: (int(r[1]), int(r[2])) for r in cur.fetchall()}
    seq = [by_id[i] for i in ids if i in by_id]
    if len(seq) != len(ids):
        return False, "missing inserted rows after rebuild"
    for k in range(1, len(seq)):
        if seq[k][0] < seq[k - 1][0] + seq[k - 1][1]:
            return False, f"overlap at element {k + 1} (ora {seq[k][0]} < prev end {seq[k - 1][0] + seq[k - 1][1]})"
    if open_b:
        cur.execute("SELECT ORA FROM TPALINSE WHERE id_tpalinse=%s", (open_b["id"],))
        if cur.fetchone()[0] > seq[0][0]:
            return False, "open bumper after first program element"
    if close_b:
        cur.execute("SELECT ORA FROM TPALINSE WHERE id_tpalinse=%s", (close_b["id"],))
        if cur.fetchone()[0] < seq[-1][0] + seq[-1][1]:
            return False, "close bumper before last program element ends"
    return True, "ok"


def run_market(conn, cod_user, d, assignment, pending):
    """Place one program assignment into one market, transaction-safe.
    Thin retry wrapper around _place_once — see its docstring for the actual
    placement logic. Retries up to _DEADLOCK_MAX_ATTEMPTS times on a 1205
    deadlock (see _is_deadlock); any other failure returns immediately since
    retrying a deterministic mismatch (bad window, missing asset, etc.) would
    just fail the same way again. Each attempt is a fresh, fully rolled-back
    transaction, so retrying from scratch is safe. A result that still failed
    on a deadlock after every attempt carries `_deadlock: True` so the caller
    can rerun that market without the parallel contention that caused it."""
    result = None
    for attempt in range(1, _DEADLOCK_MAX_ATTEMPTS + 1):
        result = _place_once(conn, cod_user, d, assignment, pending)
        if not result.pop("_deadlock", False):
            return result
        if attempt < _DEADLOCK_MAX_ATTEMPTS:
            delay = _DEADLOCK_RETRY_SECONDS * attempt + random.uniform(0.1, 1.5)
            print(f"[daily-programming] cu={cod_user} 1205 deadlock on attempt "
                  f"{attempt}/{_DEADLOCK_MAX_ATTEMPTS} ({result['message']}) — retrying "
                  f"in {delay:.1f}s")
            time.sleep(delay)
        else:
            print(f"[daily-programming] cu={cod_user} 1205 deadlock persisted after "
                  f"{_DEADLOCK_MAX_ATTEMPTS} attempts, giving up ({result['message']})")
            # Re-tag so the caller can tell "lost every deadlock coin flip" apart
            # from a deterministic failure and rerun this market without the
            # parallel contention that caused it.
            result["_deadlock"] = True
    return result


def _place_once(conn, cod_user, d, assignment, pending):
    """Place one program assignment into one market, transaction-safe.

    assignment = {mode:'explode'|'pieces'|'shoplc', fileId, fileCode, start, end,
                  pieces:[id,...], fillers:[id,...]}. 'shoplc' ignores fileId —
    the asset and per-market treatment come from the SHOP_LC map.

    `pending` is the caller's list for FILMATI syncs deferred by a lock
    conflict with another market's thread (see _sync_checksums) — the
    caller is responsible for retrying it once it's done placing.
    Returns {cu, ok, skipped, message}.
    """
    cur = conn.cursor()
    shoplc = assignment["mode"] == "shoplc"
    if shoplc and cod_user == 10:
        return {"cu": cod_user, "ok": False, "skipped": False,
                "message": "Shop LC is CTV-only — not aired on The Asian Channel"}
    lo, hi = _window(assignment["start"], assignment["end"])
    if lo is None or hi is None:
        return {"cu": cod_user, "ok": False, "skipped": False, "message": "bad time window"}
    if _is_placed(cur, cod_user, d, lo, hi):
        return {"cu": cod_user, "ok": True, "skipped": True, "message": "already placed"}

    cleared = _clear_noop_fillers(cur, cod_user, d, lo, hi)
    if cleared:
        print(f"[daily-programming] cu={cod_user} cleared {cleared} NOOP gap-filler(s) "
              f"from the target window before placing")
    slots = _slots(cur, cod_user, d, lo, hi)
    open_b, close_b = _bumpers(cur, cod_user, d, lo, hi)
    if shoplc:
        # Static setup: no profile, and any pre-placed bumpers in the window are
        # left exactly as they are — the first Shop LC piece is its own F anchor.
        open_b = close_b = None
        profile = None
        mode = "explode" if cod_user in SHOP_LC["explode"] else "single"
        fid = SHOP_LC["explode"].get(cod_user, SHOP_LC["generic"])
    else:
        profile = profile_for(assignment.get("fileCode"), assignment.get("label"))
        mode = assignment["mode"]
        fid = int(assignment["fileId"]) if mode == "explode" else None
    try:
        if mode == "explode":
            plan = _explode_plan(cur, fid, cod_user)
            if len(plan) != len(slots):
                conn.rollback()
                return {"cu": cod_user, "ok": False, "skipped": False,
                        "message": f"{len(plan)} segments vs {len(slots)} PRGS slots"}
            ids = []
            base = None
            for k, ((mi, mo), slot) in enumerate(zip(plan, slots), start=1):
                nid = _insert_event(cur, cod_user, d, slot["sched"], slot["block"], slot["seg"],
                                    slot["ora"], fid, mo - mi + 1)
                if base is None:
                    base = EVENT_BASE + nid
                cur.execute(
                    "UPDATE TPalinse SET Part=%s,TimeCode_I=%s,TimeCode_O=%s,Duration=%s,Event_P=%s WHERE ID_Tpalinse=%s",
                    (k, mi, mo, mo - mi + 1, base, nid),
                )
                cur.execute("EXEC sch_UpdateSupportAndProperties %s,%s,1", (nid, fid))
                if shoplc:
                    # Mirror master control's live-asset custom-parts explode:
                    # parts carry the WHOLE program's Ora_P/Duration_P, not their own.
                    # AFTER the SP — sch_UpdateSupportAndProperties rewrites row
                    # fields (it resets EVENT_TYPE, for one) and would clobber this.
                    cur.execute(
                        "UPDATE TPalinse SET Ora_P=0, Duration_P=%s WHERE ID_Tpalinse=%s",
                        (plan[-1][1], nid))
                ids.append(nid)
            first_id = ids[0]
        elif mode == "single":  # shoplc generic — one unexploded 6-hour live event
            # The overnight window has 6 hourly PRGS slots in every market; the
            # unexploded event goes into the first and spans the rest (exactly
            # what master control's manual drop produces: one PART=0 row at 24:00).
            if not slots:
                conn.rollback()
                return {"cu": cod_user, "ok": False, "skipped": False,
                        "message": "no PRGS slot found in the overnight window"}
            nid = _insert_event(cur, cod_user, d, slots[0]["sched"], slots[0]["block"],
                                slots[0]["seg"], slots[0]["ora"], fid, _durata(cur, fid))
            cur.execute("EXEC sch_UpdateSupportAndProperties %s,%s,1", (nid, fid))
            ids = [nid]
            first_id = nid
        else:  # pieces
            content = [int(x) for x in (assignment.get("pieces") or [])] + \
                      [int(x) for x in (assignment.get("fillers") or [])]
            if len(content) != len(slots):
                conn.rollback()
                return {"cu": cod_user, "ok": False, "skipped": False,
                        "message": f"{len(content)} pieces+fillers vs {len(slots)} PRGS slots"}
            ids = []
            for cfid, slot in zip(content, slots):
                nid = _insert_event(cur, cod_user, d, slot["sched"], slot["block"], slot["seg"],
                                    slot["ora"], cfid, _durata(cur, cfid))
                cur.execute("EXEC sch_UpdateSupportAndProperties %s,%s,1", (nid, cfid))
                ids.append(nid)
            first_id = ids[0]

        # Per-show profile: ensure required bumpers exist (insert if master control
        # didn't pre-place them). The bumper file is resolved by code at runtime.
        if profile:
            for spec, slot, label in ((profile.get("open_bumper"), slots[0], "open"),
                                      (profile.get("close_bumper"), slots[-1], "close")):
                if not spec:
                    continue
                got = _ensure_bumper(cur, cod_user, d, slot, spec,
                                     open_b if label == "open" else close_b)
                if not got:
                    conn.rollback()
                    return {"cu": cod_user, "ok": False, "skipped": False,
                            "message": f"{label} bumper {spec['code']} not found in media library"}
                if label == "open":
                    open_b = got
                else:
                    close_b = got

        # Conform type + order.
        # Open bumper present → it is the F-locked anchor, first; program parts stay T.
        # No open bumper → the first program element is the locked piece-one (F).
        if open_b:
            cur.execute("UPDATE TPalinse SET EVENT_TYPE='F' WHERE id_tpalinse=%s", (open_b["id"],))
            _ensure_after(cur, cod_user, d, first_id, open_b["xorder"])
        else:
            cur.execute("UPDATE TPalinse SET EVENT_TYPE='F' WHERE id_tpalinse=%s", (first_id,))
        if close_b:
            cur.execute("UPDATE TPalinse SET EVENT_TYPE='T' WHERE id_tpalinse=%s", (close_b["id"],))
            _close_guarantee(cur, cod_user, d, ids[-1], close_b)

        # Make the window's XORDER sequence match the intended play order —
        # Traffic_InsertEvent's neighbor-derived xorders are unreliable when the
        # window holds stale soft-deleted rows or BO-packed spots (see docstring).
        if not shoplc:
            _conform_window_xorder(cur, cod_user, d, lo, hi, ids,
                                   [s["ora"] for s in slots[:len(ids)]], open_b, close_b)

        # Profile elements (e.g. FCC IDs) that apply to this market, placed on top
        # of the program. SFO/CVC anchor flips piece A to T; DAL drops the ID into
        # the 3rd COMS break (best-effort position — the break optimizer finalizes).
        extra_ids = []
        for el in (elements_for(profile, cod_user) if profile else []):
            placed = _place_element(cur, cod_user, d, lo, hi, slots, el, first_id)
            if placed is None:
                conn.rollback()
                return {"cu": cod_user, "ok": False, "skipped": False,
                        "message": f"element {el.get('code') or el.get('id')} could not be placed "
                                   f"({el.get('segment')} break {el.get('break')})"}
            extra_ids.append(placed["id"])

        _rebuild(cur, d, cod_user, first_id)
        # Clear Etere's "needs Explode - all breakpoints" warning by re-syncing the
        # stored schedule checksum on every row we placed (parts + bumpers + elements).
        _sync_checksums(cur, list(ids) + [b["id"] for b in (open_b, close_b) if b] + extra_ids, pending)
        ok, msg = _verify_sequence(cur, ids, open_b, close_b)
        if ok:
            conn.commit()
            return {"cu": cod_user, "ok": True, "skipped": False,
                    "message": f"placed {len(ids)} elements"}
        conn.rollback()
        return {"cu": cod_user, "ok": False, "skipped": False, "message": f"verify failed: {msg}"}
    except Exception as exc:  # noqa: BLE001 - report per-market failure, leave market untouched
        conn.rollback()
        return {"cu": cod_user, "ok": False, "skipped": False, "message": f"error: {exc}",
                "_deadlock": _is_deadlock(exc)}


def list_placed_pieces(cur, cod_user, d):
    """All live pieces-mode program rows for one market/date, for the
    Replace-a-piece UI. Pieces rows are PART=0 (explode parts are 1..N),
    NEWTYPE='PGM', non-bumper. Returns dicts sorted by air order."""
    cur.execute(
        """SELECT t.id_tpalinse, t.ORA, t.ID_FILMATI, t.COD_PROGRA, t.Duration,
                  t.EVENT_TYPE, LTRIM(RTRIM(ISNULL(f.DESCRIZIO, '')))
           FROM TPALINSE t LEFT JOIN FILMATI f ON f.ID_FILMATI = t.ID_FILMATI
           WHERE t.COD_USER=%s AND t.DATA=%s AND t.NEWTYPE='PGM' AND t.LIVELLO=0
             AND t.PART=0 AND t.ID_FILMATI>0 AND t.COD_PROGRA NOT LIKE 'BUMP%%'
           ORDER BY t.ORA, t.XORDER""",
        (cod_user, d),
    )
    return [
        {"id_tpalinse": int(r[0]), "ora": int(r[1]), "fid": int(r[2]),
         "code": (r[3] or "").strip(), "duration": int(r[4] or 0),
         "event_type": (r[5] or "").strip(), "description": r[6] or ""}
        for r in cur.fetchall()
    ]


def replace_piece(conn, cod_user, d, lo, hi, old_fid, new_fid, pending):
    """Swap a placed pieces-mode program piece to a new asset — e.g. a revised
    Piece B — leaving the other pieces, bumpers, FCC IDs, and every spot in the
    window untouched. Transaction-safe with the same deadlock retry as
    run_market. Returns {cu, ok, message}.

    The target is identified by ASSET within the show's [lo, hi) time window —
    NOT by exact air time, which drifts by a few minutes market to market
    (2026-07-16: piece B aired 09:10 NYC / 09:11 SEA / 09:12 CVC+WDC). Every
    matching row in the window is swapped (a repeated filler is several rows —
    all of them are the revised file). The window bound keeps a same-day rerun
    of the same file in ANOTHER show untouched.

    Each row keeps its identity (id_tpalinse, XORDER position, Event_P) — only
    the asset binding changes: ID_FILMATI, Duration, TimeCode_O, plus
    sch_UpdateSupportAndProperties for COD_PROGRA/SUPPORTO/etc. The SP resets
    EVENT_TYPE, so the original F/T lock is restored afterwards. A rebuild
    re-flows downstream start times when the new duration differs. NOTE: the
    swapped file behaves like any fresh placement in EE — green triangle while
    the CIB servers pull it from S3, then the usual manual Explode.
    Explode-mode shows (PART>=1) are out of scope by design.
    """
    result = None
    for attempt in range(1, _DEADLOCK_MAX_ATTEMPTS + 1):
        result = _replace_piece_once(conn, cod_user, d, lo, hi, old_fid, new_fid, pending)
        if not result.pop("_deadlock", False):
            return result
        if attempt < _DEADLOCK_MAX_ATTEMPTS:
            delay = _DEADLOCK_RETRY_SECONDS * attempt + random.uniform(0.1, 1.5)
            time.sleep(delay)
    return result


def _replace_piece_once(conn, cod_user, d, lo, hi, old_fid, new_fid, pending):
    cur = conn.cursor()
    try:
        # Old fid in the WHERE is a guard: if the schedule shifted since the UI
        # loaded, we refuse rather than re-point whatever now sits there.
        cur.execute(
            """SELECT id_tpalinse, EVENT_TYPE FROM TPALINSE
               WHERE COD_USER=%s AND DATA=%s AND ORA>=%s AND ORA<%s AND ID_FILMATI=%s
                 AND NEWTYPE='PGM' AND LIVELLO=0 AND PART=0
               ORDER BY ORA""",
            (cod_user, d, lo, hi, old_fid),
        )
        rows = [(int(r[0]), (r[1] or "T").strip() or "T") for r in cur.fetchall()]
        if not rows:
            return {"cu": cod_user, "ok": False,
                    "message": "placed piece not found in this window (schedule changed since the list loaded?)"}

        new_dur = _durata(cur, new_fid)
        if not new_dur:
            return {"cu": cod_user, "ok": False,
                    "message": f"replacement asset {new_fid} has no duration"}
        cur.execute("SELECT LIVE_ID FROM FILMATI WHERE ID_FILMATI=%s", (new_fid,))
        r = cur.fetchone()
        if r and r[0] is not None:
            return {"cu": cod_user, "ok": False,
                    "message": "replacement asset is a live event — pieces must be files"}

        for rid, event_type in rows:
            cur.execute(
                "UPDATE TPALINSE SET ID_FILMATI=%s, Duration=%s, TimeCode_I=0, TimeCode_O=%s "
                "WHERE id_tpalinse=%s",
                (new_fid, new_dur, new_dur - 1, rid),
            )
            cur.execute("EXEC sch_UpdateSupportAndProperties %s,%s,1", (rid, new_fid))
            # The SP resets EVENT_TYPE — restore the original F/T lock.
            cur.execute("UPDATE TPALINSE SET EVENT_TYPE=%s WHERE id_tpalinse=%s",
                        (event_type, rid))

        _rebuild(cur, d, cod_user, rows[0][0])
        _sync_checksums(cur, [rid for rid, _ in rows], pending)

        for rid, _ in rows:
            cur.execute(
                "SELECT ID_FILMATI FROM TPALINSE WHERE id_tpalinse=%s AND LIVELLO=0",
                (rid,),
            )
            chk = cur.fetchone()
            if not chk or int(chk[0]) != int(new_fid):
                conn.rollback()
                return {"cu": cod_user, "ok": False, "message": "verify failed after swap"}
        conn.commit()
        n = len(rows)
        return {"cu": cod_user, "ok": True,
                "message": f"{n} row(s) re-pointed {old_fid} → {new_fid} ({new_dur} frames)"}
    except Exception as exc:  # noqa: BLE001 - report per-market failure, leave market untouched
        conn.rollback()
        return {"cu": cod_user, "ok": False, "message": f"error: {exc}",
                "_deadlock": _is_deadlock(exc)}


def _mkt_event_type(start: str) -> str:
    """Marketplace half-hour lock: the :00 half is the top-of-hour F anchor, the
    :30 half floats as T (matches the live DAL structure — PRG 28:30 + COM 1:30
    twice per hour)."""
    m = re.match(r"\s*\d{1,2}:(\d{2})", start or "")
    return "F" if (m and int(m.group(1)) % 60 == 0) else "T"


def fill_marketplace(conn, cod_user, d, windows, fid, pending):
    """Assign one long-form PI (whole file) into each Marketplace PRGS slot in
    `windows` — the mirror group (the noon and late-night :30 blocks that share
    the same in-hour offset), so one pick airs in both. Fills a blank slot or
    replaces whatever PI is there. Transaction-safe with the same deadlock retry
    as run_market. DAL-only in practice. Returns {ok, message, ids}."""
    result = None
    for attempt in range(1, _DEADLOCK_MAX_ATTEMPTS + 1):
        result = _fill_marketplace_once(conn, cod_user, d, windows, fid, pending)
        if not result.pop("_deadlock", False):
            return result
        if attempt < _DEADLOCK_MAX_ATTEMPTS:
            time.sleep(_DEADLOCK_RETRY_SECONDS * attempt + random.uniform(0.1, 1.5))
    return result


def _fill_marketplace_once(conn, cod_user, d, windows, fid, pending):
    cur = conn.cursor()
    try:
        new_dur = _durata(cur, fid)
        if not new_dur:
            return {"ok": False, "message": f"asset {fid} has no duration"}
        cur.execute("SELECT LIVE_ID FROM FILMATI WHERE ID_FILMATI=%s", (fid,))
        r = cur.fetchone()
        if r and r[0] is not None:
            return {"ok": False, "message": "asset is a live event — Marketplace PIs must be files"}

        ids = []
        for start, end in windows:
            lo, hi = _window(start, end)
            if lo is None or hi is None:
                conn.rollback()
                return {"ok": False, "message": f"bad window {start}-{end}"}
            slots = _slots(cur, cod_user, d, lo, hi, "PRGS")
            if len(slots) != 1:
                conn.rollback()
                return {"ok": False, "message": f"{start}-{end}: expected 1 PRGS slot, found {len(slots)}"}
            slot = slots[0]
            ev = _mkt_event_type(start)
            # Current live whole-file PGM occupant(s) in this :30 window.
            cur.execute(
                """SELECT id_tpalinse FROM TPALINSE
                   WHERE COD_USER=%s AND DATA=%s AND ORA>=%s AND ORA<%s
                     AND NEWTYPE='PGM' AND LIVELLO=0 AND PART=0 AND ID_FILMATI>0
                     AND COD_PROGRA NOT LIKE 'BUMP%%'
                   ORDER BY ORA""",
                (cod_user, d, lo, hi),
            )
            occ = [int(x[0]) for x in cur.fetchall()]
            if len(occ) > 1:
                conn.rollback()
                return {"ok": False,
                        "message": f"{start}-{end}: {len(occ)} occupants in one slot — resolve manually"}
            if occ:  # replace in place (keeps identity/XORDER, like _replace_piece_once)
                rid = occ[0]
                cur.execute(
                    "UPDATE TPALINSE SET ID_FILMATI=%s, Duration=%s, TimeCode_I=0, TimeCode_O=%s "
                    "WHERE id_tpalinse=%s",
                    (fid, new_dur, new_dur - 1, rid),
                )
                cur.execute("EXEC sch_UpdateSupportAndProperties %s,%s,1", (rid, fid))
                cur.execute("UPDATE TPALINSE SET EVENT_TYPE=%s WHERE id_tpalinse=%s", (ev, rid))
                nid = rid
            else:  # blank slot → insert the whole file
                nid = _insert_event(cur, cod_user, d, slot["sched"], slot["block"], slot["seg"],
                                    slot["ora"], fid, new_dur)
                cur.execute("EXEC sch_UpdateSupportAndProperties %s,%s,1", (nid, fid))
                cur.execute("UPDATE TPALINSE SET EVENT_TYPE=%s WHERE id_tpalinse=%s", (ev, nid))
            ids.append(nid)

        if not ids:
            conn.rollback()
            return {"ok": False, "message": "no Marketplace slots resolved for this day"}
        _rebuild(cur, d, cod_user, min(ids))
        _sync_checksums(cur, ids, pending)
        for nid in ids:
            cur.execute("SELECT ID_FILMATI FROM TPALINSE WHERE id_tpalinse=%s AND LIVELLO=0", (nid,))
            chk = cur.fetchone()
            if not chk or int(chk[0]) != int(fid):
                conn.rollback()
                return {"ok": False, "message": "verify failed after placement"}
        conn.commit()
        return {"ok": True, "message": f"placed into {len(ids)} slot(s)", "ids": ids}
    except Exception as exc:  # noqa: BLE001 - report failure, leave the day untouched
        conn.rollback()
        return {"ok": False, "message": f"error: {exc}", "_deadlock": _is_deadlock(exc)}


def _prgs_duration_total(cur, cod_user, d, lo, hi):
    """Sum of PRGS segment durations (frames) in the window — the block's
    programming capacity. Mirrors _slots' segment resolution."""
    cur.execute(
        """SELECT ISNULL(SUM(seg.Duration),0)
           FROM traffic_calendar ca WITH(NOLOCK)
           JOIN traffic_scheduleblock sb WITH(NOLOCK) ON sb.ID_TrafficSchedule=ca.ID_TrafficSchedule
           JOIN traffic_block bl WITH(NOLOCK) ON bl.ID_TrafficBlock=sb.ID_TrafficBlock
           OUTER APPLY (
             SELECT si.Offset,si.Type,si.Duration FROM trf_instancesegment si WITH(NOLOCK)
               WHERE si.ID_TrafficBlock=bl.ID_TrafficBlock AND si.COD_USER=ca.Cod_User AND si.INSTANCEDATE=ca.Date AND si.visible=1
             UNION
             SELECT se.Offset,se.Type,se.Duration FROM traffic_segment se WITH(NOLOCK)
               WHERE se.ID_TrafficBlock=bl.ID_TrafficBlock AND se.visible=1
               AND (SELECT COUNT(*) FROM trf_instancesegment WITH(NOLOCK) WHERE ID_TrafficBlock=bl.ID_TrafficBlock AND COD_USER=ca.Cod_User AND INSTANCEDATE=ca.Date)=0
           ) seg
           WHERE ca.Cod_User=%s AND ca.Date=%s AND bl.expired=0 AND seg.Type='PRGS'
             AND (sb.offset+seg.Offset)>=%s AND (sb.offset+seg.Offset)<%s""",
        (cod_user, d, lo, hi),
    )
    return int(cur.fetchone()[0] or 0)


def ordered_pieces(cur, base):
    """Ordered A/B/C… piece fids for a show base code (COD_PROGRA = base+letter)."""
    cur.execute(
        "SELECT ID_FILMATI, COD_PROGRA FROM FILMATI WITH(NOLOCK) "
        "WHERE NEWTYPE='PGM' AND COD_PROGRA LIKE %s ORDER BY COD_PROGRA",
        (base + "%",),
    )
    return [int(r[0]) for r in cur.fetchall()
            if len(r[1]) == len(base) + 1 and r[1][-1:].isalpha()]


def place_weekend_drama(conn, cod_user, d, start, end, piece_fids, filler_fids, pending):
    """Weekend Korean-drama setup for one market: drama pieces one-per-PRGS-slot
    in block order (Drama 1 → slots 1-3, …), then the fillers STACKED into the
    last PRGS slot behind the final piece (floating). First piece = F anchor, rest
    = T. COMS is left alone (commercials conform in EE afterward). Transaction-safe
    with the same deadlock retry as run_market. Returns {cu, ok, skipped, message}."""
    result = None
    for attempt in range(1, _DEADLOCK_MAX_ATTEMPTS + 1):
        result = _place_weekend_drama_once(conn, cod_user, d, start, end, piece_fids, filler_fids, pending)
        if not result.pop("_deadlock", False):
            return result
        if attempt < _DEADLOCK_MAX_ATTEMPTS:
            time.sleep(_DEADLOCK_RETRY_SECONDS * attempt + random.uniform(0.1, 1.5))
    return result


def _place_weekend_drama_once(conn, cod_user, d, start, end, piece_fids, filler_fids, pending):
    cur = conn.cursor()
    try:
        lo, hi = _window(start, end)
        if lo is None or hi is None:
            return {"cu": cod_user, "ok": False, "skipped": False, "message": "bad time window"}
        if _is_placed(cur, cod_user, d, lo, hi):
            return {"cu": cod_user, "ok": True, "skipped": True, "message": "already placed"}
        _clear_noop_fillers(cur, cod_user, d, lo, hi)
        slots = _slots(cur, cod_user, d, lo, hi)
        if len(slots) != len(piece_fids):
            conn.rollback()
            return {"cu": cod_user, "ok": False, "skipped": False,
                    "message": f"{len(piece_fids)} drama pieces vs {len(slots)} PRGS slots"}
        ids, keys = [], []
        for fid, slot in zip(piece_fids, slots):
            nid = _insert_event(cur, cod_user, d, slot["sched"], slot["block"], slot["seg"],
                                slot["ora"], fid, _durata(cur, fid))
            cur.execute("EXEC sch_UpdateSupportAndProperties %s,%s,1", (nid, fid))
            ids.append(nid)
            keys.append(slot["ora"])
        last = slots[-1]
        for i, fid in enumerate(filler_fids):
            nid = _insert_event(cur, cod_user, d, last["sched"], last["block"], last["seg"],
                                last["ora"], fid, _durata(cur, fid))
            cur.execute("EXEC sch_UpdateSupportAndProperties %s,%s,1", (nid, fid))
            ids.append(nid)
            keys.append(last["ora"] + i + 1)  # sort after the last piece, in draw order
        if not ids:
            conn.rollback()
            return {"cu": cod_user, "ok": False, "skipped": False, "message": "no pieces to place"}
        cur.execute("UPDATE TPalinse SET EVENT_TYPE='F' WHERE id_tpalinse=%s", (ids[0],))
        for nid in ids[1:]:
            cur.execute("UPDATE TPalinse SET EVENT_TYPE='T' WHERE id_tpalinse=%s", (nid,))
        # Order the window so pieces play in slot order and fillers trail the last piece.
        _conform_window_xorder(cur, cod_user, d, lo, hi, ids, keys, None, None)
        _rebuild(cur, d, cod_user, ids[0])
        _sync_checksums(cur, ids, pending)
        ok, msg = _verify_sequence(cur, ids, None, None)
        if ok:
            conn.commit()
            return {"cu": cod_user, "ok": True, "skipped": False,
                    "message": f"placed {len(piece_fids)} pieces + {len(filler_fids)} filler(s)"}
        conn.rollback()
        return {"cu": cod_user, "ok": False, "skipped": False, "message": f"verify failed: {msg}"}
    except Exception as exc:  # noqa: BLE001 - report per-market failure, leave market untouched
        conn.rollback()
        return {"cu": cod_user, "ok": False, "skipped": False, "message": f"error: {exc}",
                "_deadlock": _is_deadlock(exc)}


def sweep_daily_ids(conn, cod_user, dates, pending):
    """Place this market's standing daily elements (see show_profiles
    daily_elements — e.g. the DAL end-of-day FCC children's-records ID) for
    every date in `dates`. Each date is independent and idempotent: skipped if
    the element is already live anywhere that broadcast day, or if the day's
    schedule isn't published yet (the next run's sweep picks it up). Returns a
    list of {date, ok, skipped, message} — empty when the market has no daily
    elements configured, so callers can omit the result row entirely."""
    out = []
    for el in daily_elements(cod_user):
        for d in dates:
            result = None
            for attempt in range(1, _DEADLOCK_MAX_ATTEMPTS + 1):
                result = _place_daily_once(conn, cod_user, d, el, pending)
                if not result.pop("_deadlock", False):
                    break
                if attempt < _DEADLOCK_MAX_ATTEMPTS:
                    delay = _DEADLOCK_RETRY_SECONDS * attempt + random.uniform(0.1, 1.5)
                    print(f"[daily-programming] cu={cod_user} daily-ID 1205 deadlock on "
                          f"attempt {attempt}/{_DEADLOCK_MAX_ATTEMPTS} for {d} — retrying "
                          f"in {delay:.1f}s")
                    time.sleep(delay)
            out.append(result)
    return out


def _place_daily_once(conn, cod_user, d, el, pending):
    """One attempt to place one daily element on one date, transaction-safe.

    Target = the last COMS break starting before midnight (ORA < DAY_FRAMES;
    the broadcast day runs 06:00→30:00, so post-midnight breaks sit at 24h+ and
    are never picked). The element goes in at the break's start as EVENT_TYPE
    'T' — it floats, so break optimization / master control can settle it as
    the final item aired in the calendar day.

    The already-placed check deliberately has NO ORA bound: a programming gap
    ahead of the ID pushes the whole tail past midnight (ORA 24h+, same DATA),
    and an ORA<24:00 check then misses the existing ID — each sweep of the
    date placed another copy (SFO/CVC 2026-07-17: three IDs, two hand-deleted).
    Any live copy anywhere on the broadcast date means already placed."""
    cur = conn.cursor()
    label = str(d)
    try:
        fid = el.get("id")
        if not fid:
            cur.execute("SELECT ID_FILMATI FROM FILMATI WHERE COD_PROGRA=%s", (el.get("code"),))
            r = cur.fetchone()
            if not r:
                conn.rollback()
                return {"date": label, "ok": False, "skipped": False,
                        "message": f"asset {el.get('code')} not found"}
            fid = r[0]
        fid = int(fid)
        cur.execute(
            """SELECT COUNT(*) FROM TPALINSE WHERE COD_USER=%s AND DATA=%s
               AND ID_FILMATI=%s AND LIVELLO=0""",
            (cod_user, d, fid),
        )
        if cur.fetchone()[0] > 0:
            conn.rollback()
            return {"date": label, "ok": True, "skipped": True, "message": "already placed"}
        slots = _slots(cur, cod_user, d, _frames("06:00"), DAY_FRAMES, "COMS")
        if not slots:
            conn.rollback()
            return {"date": label, "ok": True, "skipped": True, "message": "not published yet"}
        slot = slots[-1]
        nid = _insert_event(cur, cod_user, d, slot["sched"], slot["block"], slot["seg"],
                            slot["ora"], fid, _durata(cur, fid))
        cur.execute("EXEC sch_UpdateSupportAndProperties %s,%s,1", (nid, fid))
        cur.execute("UPDATE TPalinse SET EVENT_TYPE=%s WHERE id_tpalinse=%s",
                    (el.get("event_type", "T"), nid))
        _rebuild(cur, d, cod_user, nid)
        _sync_checksums(cur, [nid], pending)
        conn.commit()
        return {"date": label, "ok": True, "skipped": False, "message": "placed"}
    except Exception as exc:  # noqa: BLE001 - report per-date failure, leave the day untouched
        conn.rollback()
        return {"date": label, "ok": False, "skipped": False, "message": f"error: {exc}",
                "_deadlock": _is_deadlock(exc)}
