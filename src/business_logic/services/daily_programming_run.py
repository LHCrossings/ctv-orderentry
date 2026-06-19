"""Deterministic placement engine for the Set up Daily Programming tool.

Places a program into the PRGS (program) segments of one market's schedule,
mirroring what Etere's drag + Explode does, but deterministically and
transaction-safe. Two paths:

  * explode  – one EDL-marked file split into N segments (dbo.ExplodeEdl plan),
               grouped under Part-1's Event_P, timecodes carved per segment.
  * pieces   – N standalone whole-file pieces (a/b/c…) + fillers, each its own
               event, placed one per PRGS slot in order (shows first, fillers next).

Both honour the open/close bumper rules and the slot/break count. Every market
is committed only if it passes the verify gate, else rolled back untouched.

See tasks/daily_programming_discovery.md + the project memory for the recipe.
"""
from __future__ import annotations

import re

from src.business_logic.services.show_profiles import profile_for

FPS = 29.97
EVENT_BASE = 100000000000  # Event_P = EVENT_BASE + id_tpalinse


def _frames(hhmm: str):
    m = re.match(r"\s*(\d{1,2}):(\d{2})", hhmm or "")
    return int((int(m.group(1)) * 3600 + int(m.group(2)) * 60) * FPS) if m else None


def _prgs_slots(cur, cod_user, d, lo, hi):
    """PRGS slots in the window, ordered by start time: list of dicts."""
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
           WHERE ca.Cod_User=%s AND ca.Date=%s AND bl.expired=0 AND seg.Type='PRGS'
             AND (sb.offset+seg.Offset)>=%s AND (sb.offset+seg.Offset)<%s
           ORDER BY st""",
        (cod_user, d, lo, hi),
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

    assignment = {mode:'explode'|'pieces', fileId, fileCode, start, end,
                  pieces:[id,...], fillers:[id,...]}.
    Returns {cu, ok, skipped, message}.
    """
    cur = conn.cursor()
    lo, hi = _frames(assignment["start"]), _frames(assignment["end"])
    if lo is None or hi is None:
        return {"cu": cod_user, "ok": False, "skipped": False, "message": "bad time window"}
    if _is_placed(cur, cod_user, d, lo, hi):
        return {"cu": cod_user, "ok": True, "skipped": True, "message": "already placed"}

    slots = _prgs_slots(cur, cod_user, d, lo, hi)
    open_b, close_b = _bumpers(cur, cod_user, d, lo, hi)
    profile = profile_for(assignment.get("fileCode"))
    try:
        if assignment["mode"] == "explode":
            fid = int(assignment["fileId"])
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
                ids.append(nid)
            first_id = ids[0]
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

        _rebuild(cur, d, cod_user, first_id)
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
