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

import re

from src.business_logic.services.show_profiles import elements_for, profile_for

FPS = 29.97
EVENT_BASE = 100000000000  # Event_P = EVENT_BASE + id_tpalinse


DAY_FRAMES = int(24 * 3600 * FPS)  # one broadcast-day's worth of frames

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


def _is_placed(cur, cod_user, d, lo, hi):
    cur.execute(
        """SELECT COUNT(*) FROM TPALINSE WHERE COD_USER=%s AND DATA=%s AND ORA>=%s AND ORA<%s
           AND NEWTYPE='PGM' AND LIVELLO=0 AND ID_FILMATI>0 AND COD_PROGRA NOT LIKE 'BUMP%%'""",
        (cod_user, d, lo, hi),
    )
    return cur.fetchone()[0] > 0


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


def _sync_checksums(cur, ids):
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
    """
    # Normalise the checksum-input fields on each distinct FILMATI to their
    # canonical settled state, so the checksum we compute below is the value the
    # file converges to after Aligner pulls it down.
    fids = set()
    for rid in ids:
        if rid is None:
            continue
        cur.execute("SELECT ID_FILMATI FROM TPALINSE WHERE id_tpalinse=%s", (rid,))
        row = cur.fetchone()
        if row and row[0]:
            fids.add(row[0])
    live_fids = set()
    for fid in fids:
        cur.execute("SELECT LIVE_ID FROM FILMATI WHERE ID_FILMATI=%s", (fid,))
        row = cur.fetchone()
        if row and row[0] is not None:
            # Live event: LIVE_ID is the live-feed link and the asset never goes
            # through the Aligner — its fields as-is are the settled state.
            live_fids.add(fid)
            continue
        cur.execute("""
            UPDATE FILMATI SET
                INF_DIGIT=0,
                AUDIO=NULL,
                AUDIO_LANGUAGE=NULL,
                LIVE_ID=NULL
            WHERE ID_FILMATI=%s
        """, (fid,))

    for rid in ids:
        if rid is None:
            continue
        cur.execute("SELECT COD_PROGRA, ID_FILMATI FROM TPALINSE WHERE id_tpalinse=%s", (rid,))
        row = cur.fetchone()
        prog_code = row[0] if row and row[0] else ""
        rid_fid = row[1] if row else None

        if rid_fid in live_fids:
            # Live-asset row: sch_UpdateSupportAndProperties already wrote the REAL
            # supporto ('0LIVE<channel>' — the live-feed binding EE renders as the
            # LIVE badge / '1-Channel 1' source) and CRAWL_DESC (the station-ID
            # overlay config on CVC/SFO Shop LC). The Explode-mimicking cosmetics
            # below would DESTROY both (root cause of the 2026-07-08 Shop LC
            # dead-air incident) — freeze the checksum only.
            cur.execute(
                "UPDATE TPALINSE SET SCHEDULE_CHECKSUM = dbo.sch_getFilmatiCheckSum(%s) WHERE id_tpalinse=%s",
                (rid, rid))
            continue

        # Cosmetic metadata (mirrors Explode's UI appearance), then freeze the
        # checksum against the now-canonical FILMATI input fields.
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


def _rebuild(cur, d, cod_user, fromid):
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


def run_market(conn, cod_user, d, assignment):
    """Place one program assignment into one market, transaction-safe.

    assignment = {mode:'explode'|'pieces'|'shoplc', fileId, fileCode, start, end,
                  pieces:[id,...], fillers:[id,...]}. 'shoplc' ignores fileId —
    the asset and per-market treatment come from the SHOP_LC map.
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
        _sync_checksums(cur, list(ids) + [b["id"] for b in (open_b, close_b) if b] + extra_ids)
        ok, msg = _verify_sequence(cur, ids, open_b, close_b)
        if ok:
            conn.commit()
            return {"cu": cod_user, "ok": True, "skipped": False,
                    "message": f"placed {len(ids)} elements"}
        conn.rollback()
        return {"cu": cod_user, "ok": False, "skipped": False, "message": f"verify failed: {msg}"}
    except Exception as exc:  # noqa: BLE001 - report per-market failure, leave market untouched
        conn.rollback()
        return {"cu": cod_user, "ok": False, "skipped": False, "message": f"error: {exc}"}
