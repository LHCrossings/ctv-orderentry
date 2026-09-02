"""Fill & Finish — plan and apply one program window (spec: tasks/finish-hour.md v0.3).

Shared by the CLI (`scripts/finish_apply.py`) and the Control Room page
(`src/web/routes/finish.py`). ONE code path: what the page previews is what it writes.

apply_hour() runs the whole thing in ONE transaction:
  deletes  → LIVELLO=666 + trafficPalinse row removed (Etere's own convention)
  inserts  → Traffic_InsertEvent into the break's COMS segment (the FCC daily-ID recipe)
             + EVENT_TYPE='T', NOTE='CTV_FINISH' ownership tag, XORDER between neighbours
  NOOPs    → the hour's live NOOP gap-fillers soft-deleted (stale after any edit)
  rebuild  → sch_rebuildStartTimeSchedule with @shiftup=1 (EE-style packing)
  bind     → SUPPORTO = prefix + FS_FILMATI.FILE_ID, written LAST (both Etere SPs
             overwrite it with COD_PROGRA; no row bound any other way has ever aired)
  verify   → binding, no trafficPalinse, no live NOOP, packed end == plan → else ROLLBACK
Restore SQL is written before anything is touched. Live-proven 2026-08-28 (LAX/CVC/WDC/MMT).
"""

from __future__ import annotations

import os
from datetime import datetime

from src.business_logic.services.daily_programming_run import (
    _durata,
    _insert_event,
    _slots,
    _sync_checksums,
)
from src.business_logic.services.finish_plan import (
    FPS,
    ID_MIN_AIR,
    Ev,
    Filler,
    hms,
    load_day,
    load_inventory,
    plan,
    window_from_day,
)

OVERRUN_SECONDS = 30.0  # content past the slot end by more than this = programming problem
UNPLACED_SECONDS = 300.0  # > 5 min of true remainder = programming still missing, not a fill job

RESTORE_DIR = os.environ.get("CTV_FINISH_RESTORE_DIR", os.path.join("logs", "finish-restore"))


def _rebuild_shiftup(cur, d, cod_user, fromid):
    """sch_rebuildStartTimeSchedule with @shiftup=1 @shiftupInsideProgram=1: pack
    every 'T' row up behind its predecessor (what EE does on delete). Daily
    Programming's call passes @shiftup=0, which fills the hole with a NOOP instead
    (CVC 8/28 dry run: deleted :60 left a 60s NOOP, tail did not move)."""
    cur.execute(
        "EXEC dbo.sch_rebuildStartTimeSchedule %s,%s,0,0,NULL,%s,-1,1,1,0", (d, cod_user, fromid)
    )
    try:
        while cur.nextset():
            pass
    except Exception:
        pass


def _explode_window(cur, market: int, date: str, lo_f: int, hi_f: int) -> dict:
    """Etere's "Explode - all breakpoints" for the window, so a finished show carries
    no yellow triangles (Lee 9/1: Finish must clear ALL of them, not just the rows it
    touched). EE flags a row for either of two things, and Explode fixes both:

    1. Event in/out outside the asset: the scheduler writes every commercial with
       TIMECODE_O = POS_FIN + 1 (its DURATION is the nominal length, so a short asset
       overruns by 2-3 frames) — CVC 9/2 had 183 such rows, every one a triangle,
       while the same assets in the 8 markets MC had exploded read
       TIMECODE_I/O = POS_INI/POS_FIN, DURATION = POS_FIN - POS_INI + 1. Program
       PARTs are sub-ranges of one file and stay inside the asset, so PART=0 only.
       Live assets (LIVE_ID set) are never conformed.
    2. Stored SCHEDULE_CHECKSUM != live: a row placed before its file reached the
       CIBs keeps a pre-download value. Storing the live value IS Explode.

    Runs BEFORE plan_window (the planner reads DURATION) and touches TPALINSE only —
    never FILMATI. A DURATION change needs a start-time rebuild; the caller does it."""
    cur.execute(
        """UPDATE t SET t.TIMECODE_I = f.POS_INI, t.TIMECODE_O = f.POS_FIN,
                        t.DURATION = f.POS_FIN - f.POS_INI + 1
           FROM TPALINSE t JOIN FILMATI f ON f.ID_FILMATI = t.ID_FILMATI
           WHERE t.COD_USER=%s AND t.DATA=%s AND t.LIVELLO=0 AND t.ORA>=%s AND t.ORA<%s
             AND t.PART = 0 AND t.NEWTYPE <> 'NOOP' AND f.LIVE_ID IS NULL
             AND (t.TIMECODE_I <> f.POS_INI OR t.TIMECODE_O <> f.POS_FIN
                  OR t.DURATION <> f.POS_FIN - f.POS_INI + 1)""",
        (market, date, lo_f, hi_f),
    )
    n_tc = cur.rowcount
    cur.execute(
        """UPDATE TPALINSE SET SCHEDULE_CHECKSUM = dbo.sch_getFilmatiCheckSum(ID_TPALINSE)
           WHERE COD_USER=%s AND DATA=%s AND LIVELLO=0 AND ORA>=%s AND ORA<%s
             AND ISNULL(SCHEDULE_CHECKSUM,0) <> ISNULL(dbo.sch_getFilmatiCheckSum(ID_TPALINSE),0)""",
        (market, date, lo_f, hi_f),
    )
    return {"timecodes": n_tc, "checksums": cur.rowcount}


def _supporto(cur, filmati: int) -> str:
    """Playout binding = channel prefix + FS_FILMATI.FILE_ID (same rule as
    orders._pi_filler_supporto). NEVER truncate short of the column:
    TPALINSE.SUPPORTO is varchar(42), and a clipped binding cannot resolve to a
    file — DAL's station ID ('0ETX      ID - TACDAL - GENERIC', 31 chars) was
    cut to 30 here, so every DAL Finish rolled back on its own verify (Maija 9/1;
    the aired hand-placed siblings all carry the full 31 chars, STATUS='A')."""
    cur.execute(
        "SELECT TOP 1 ISNULL(d.LEGACY_BASESUPP, CAST(d.LEGACY_MEDIAID AS VARCHAR) + 'ETX      '), ff.FILE_ID"
        " FROM FS_FILMATI ff JOIN FS_METADEVICE d ON d.ID_METADEVICE = ff.ID_METADEVICE"
        " WHERE ff.ID_FILMATI = %s AND d.LEGACY_MEDIAID IS NOT NULL ORDER BY d.LEGACY_MEDIAID",
        (int(filmati),),
    )
    r = cur.fetchone()
    if not r or not r[1]:
        raise RuntimeError(f"no FS_FILMATI FILE_ID for {filmati}")
    sup = str(r[0]) + str(r[1])
    if len(sup) > 42:
        raise RuntimeError(f"SUPPORTO overflows varchar(42), cannot bind: {sup!r}")
    return sup


def _item_dict(it, start: float, tag: str) -> dict:
    kind = getattr(it, "newtype", None) or getattr(it, "kind", "")
    return {
        "ora": start,
        "time": hms(start),
        "dur": it.dur,
        "type": kind,
        "desc": it.desc,
        "tag": tag,  # pgm | paid | keep | new | del
        "id": getattr(it, "id", None),
        "filmati": getattr(it, "filmati", None),
    }


def plan_window(
    cur, market: int, date: str, lo: float, hi: float, rows=None, refill: bool = False
) -> dict:
    """Read-only: the packed timeline Finish would produce, plus the edit list.
    `finished` is DERIVED: nothing to delete/insert except re-placing the ID that is
    already there (planner reports 0 edits + ID present).

    `refill` (Lee 9/1): strip every existing PI/PSA/ID the window holds and plan the
    fill from scratch — what Lee did by hand on CVC 9/2 10:00 (delete the PIs and the
    ID, click Finish) to get a show with no yellow triangles. Paid spots and programs
    are never touched; the pre-strip remainder still decides "unplaced"."""
    rows = rows if rows is not None else load_day(cur, market, date)
    evs_all = window_from_day(rows, lo, hi)
    strip: list[Ev] = [e for e in evs_all if e.is_fill] if refill else []
    evs = [e for e in evs_all if not e.is_fill] if refill else evs_all
    if not evs or not any(e.is_program for e in evs):
        return {
            "ok": False,
            "state": "unplaced",
            "remainder": hi - lo,
            "error": "no programming placed",
            "timeline": [],
            "edits": [],
            "n_delete": 0,
            "n_insert": 0,
        }
    # Is the window's end a fixed (F) event? If the next show simply FOLLOWS (a
    # half-hour boundary with no F anchor), there is nothing to finish here: the
    # hour's ID belongs to the window that ends at the F event.
    hi_fixed = any(
        str(r[4] or "").strip() == "F" and abs(r[1] - int(hi * FPS)) <= FPS for r in rows
    )
    # No F at `hi` and no program placed there either: the next show is simply not
    # placed yet. Assume it will start at `hi` (Lee 9/1) so the hour can be finished
    # now; when Daily Programming places it, its F event cuts the ID exactly there.
    next_placed = any(str(r[3]).strip() == "PGM" and abs(r[1] - int(hi * FPS)) <= FPS for r in rows)
    assume_fixed = not hi_fixed and not next_placed
    hi_fixed = hi_fixed or assume_fixed
    inv = load_inventory(cur, market, date)
    breaks, notes = plan(evs, inv, hi, market)
    if refill:
        notes.insert(0, f"refill: {len(strip)} existing PI/PSA/ID rows removed, fill re-planned")
    if assume_fixed:
        notes.append(f"next show not placed yet — assuming it starts at {hms(hi)}")
    # An existing Station ID of the right asset is KEPT, not deleted and re-added:
    # the plan's ID slot is taken by the live row (new PSAs go in ahead of it via
    # XORDER) so the page shows a nudge as 0 remove / 1 add (Lee 8/28).
    old_ids = [e for e in evs if e.newtype == "ID"]
    final = breaks[-1]
    planned_id = next((x for x in final.items if isinstance(x, Filler) and x.kind == "ID"), None)
    extra_deletes: list[Ev] = []
    if old_ids and planned_id is not None and old_ids[-1].filmati == planned_id.filmati:
        final.items.remove(planned_id)
        final.items.append(old_ids[-1])
        extra_deletes = old_ids[:-1]  # a second ID in the hour (per-show habit) goes
    else:
        extra_deletes = old_ids  # wrong asset (or none) -> re-placed by the planned ID
    pieces = [e for e in evs if e.is_program]

    def _actual_break(e: Ev) -> int:
        idx = -1
        for i, pc in enumerate(pieces):
            if e.ora >= pc.end - 0.5:
                idx = i
        return idx

    moves = [
        (x, b.after_piece_idx)
        for b in breaks
        for x in b.items
        if isinstance(x, Ev) and not x.is_program and _actual_break(x) != b.after_piece_idx
    ]
    planned = {id(x) for b in breaks for x in b.items}
    deletes = (
        [e for e in evs if e.newtype != "ID" and e.is_fill and id(e) not in planned]
        + extra_deletes
        + strip
    )
    inserts = [(b, x) for b in breaks for x in b.items if isinstance(x, Filler)]
    cannot = any(n.startswith("⚠") for n in notes)
    id_only = not deletes and not inserts and not moves  # nothing to write
    finished = id_only and not cannot

    timeline, edits = [], []
    t = pieces[0].ora
    for i, p in enumerate(pieces):
        timeline.append(_item_dict(p, t, "pgm"))
        t += p.dur
        b = next((x for x in breaks if x.after_piece_idx == i), None)
        if not b:
            continue
        b_start = t
        for it in b.items:
            if isinstance(it, Filler):
                tag = "new"
                edits.append({"op": "insert", **_item_dict(it, t, tag), "break": i})
            else:
                tag = (
                    "move" if any(it is m for m, _ in moves) else ("keep" if it.is_fill else "paid")
                )
            timeline.append({**_item_dict(it, t, tag), "break": i})
            t += it.dur
        timeline[-1]["break_len"] = t - b_start
        timeline[-1]["break_end"] = True
    for e in deletes:
        edits.insert(0, {"op": "delete", **_item_dict(e, e.ora, "del")})
    for e, tgt in moves:
        edits.append({"op": "move", **_item_dict(e, e.ora, "move"), "break": tgt})
    actual_end = max(e.end for e in evs_all)
    rem_s = hi - pieces[0].ora - sum(e.dur for e in evs_all if e.newtype not in ("ID", "NOOP"))
    if len(pieces) == 1 and not breaks[0].items if breaks else True:
        state = (
            "na"  # a single fixed event with no breaks (overnight live feed) — nothing to finish
        )
    elif rem_s > UNPLACED_SECONDS:
        state = "unplaced"  # the precondition (all programming placed) is not met
    elif rem_s < -OVERRUN_SECONDS:
        state = "overrun"  # more content than the slot holds — a programming problem, not a fill
    elif not hi_fixed:
        state = "follows"  # next show follows directly; the ID lands at the hour's F event
    elif cannot:
        state = "cannot"
    elif finished:
        state = "finished"
    else:
        state = "ready"
    id_airs = next(
        (float(n.split("airs ")[1].split("s")[0]) for n in notes if "ID" in n and "airs" in n), None
    )
    return {
        "ok": not cannot and state in ("ready", "finished"),
        "state": state,
        "remainder": rem_s,
        "finished": finished,
        "notes": notes,
        "timeline": timeline,
        "edits": edits,
        "planned_end": t,
        "planned_end_hms": hms(t),
        "actual_end": actual_end,
        "actual_end_hms": hms(actual_end),
        "window_end": hi,
        "overrun": actual_end - hi,
        "id_airs": id_airs,
        "n_delete": len(deletes),
        "n_move": len(moves),
        "n_insert": len(inserts),
        "_breaks": breaks,
        "_evs": evs,
        "_deletes": deletes,
        "_moves": moves,
        "_inserts": inserts,
        "_id_only": id_only,
    }


def _guide_secs(txt: str) -> float | None:
    """'HH:MM' / 'HH:MM:SS' guide time -> broadcast seconds (hours < 6 are post-midnight)."""
    try:
        parts = [int(x) for x in str(txt).strip().split(":")]
    except ValueError:
        return None
    if len(parts) < 2:
        return None
    sec = parts[0] * 3600 + parts[1] * 60 + (parts[2] if len(parts) > 2 else 0)
    return float(sec + 24 * 3600 if parts[0] < 6 else sec)


def day_windows(market: int, date: str, rows: list[tuple]) -> list[dict]:
    """The day's program windows: from the K: programming grid (same source as
    Daily Programming — real titles, language, half-hour slots) when it can be read,
    else from Etere's F anchors (file codes)."""
    import datetime as _dt

    from src.business_logic.services.finish_plan import day_programs
    from src.business_logic.services.programming_grid import get_day_programs

    network = "TAC" if market == 10 else "CTV"
    out = []
    try:
        grid = get_day_programs(network, _dt.date.fromisoformat(date))
    except Exception:  # noqa: BLE001 — grid unreadable → fallback below
        grid = {"found": False}
    if grid.get("found"):
        for g in grid.get("programs", []):
            lo, hi = _guide_secs(g.get("start")), _guide_secs(g.get("end"))
            if lo is None or hi is None:
                continue
            if hi <= lo:
                hi += 24 * 3600
            out.append(
                {
                    "lo": lo,
                    "hi": hi,
                    "title": g.get("title") or "",
                    "language": g.get("language") or "",
                    "source": "grid",
                }
            )
    if not out:
        for p in day_programs(rows):
            out.append({**p, "language": "", "source": "etere"})
    return out


def _triangle_oras(cur, market: int, date: str) -> list[int]:
    """ORA of every live row EE would flag with the yellow triangle today: event
    in/out outside its asset, or stored checksum != live (the two Explode triggers,
    see _explode_window). One pass per day — the checksum UDF is not cheap."""
    cur.execute(
        """SELECT t.ORA FROM TPALINSE t JOIN FILMATI f ON f.ID_FILMATI = t.ID_FILMATI
           WHERE t.COD_USER=%s AND t.DATA=%s AND t.LIVELLO=0 AND t.NEWTYPE <> 'NOOP'
             AND (t.TIMECODE_I < f.POS_INI OR t.TIMECODE_O > f.POS_FIN
                  OR ISNULL(t.SCHEDULE_CHECKSUM,0) <> ISNULL(dbo.sch_getFilmatiCheckSum(t.ID_TPALINSE),0))""",
        (market, date),
    )
    return [int(r[0]) for r in cur.fetchall()]


def list_programs(cur, market: int, date: str) -> list[dict]:
    """The day's program windows with the derived Finish state for each."""
    rows = load_day(cur, market, date)
    tri = _triangle_oras(cur, market, date)
    out = []
    for p in day_windows(market, date, rows):
        r = plan_window(cur, market, date, p["lo"], p["hi"], rows=rows)
        evs = window_from_day(rows, p["lo"], p["hi"])
        code = next((e.desc for e in evs if e.is_program and "BUMP" not in e.desc.upper()), "")
        lo_f, hi_f = int(p["lo"] * FPS), int(p["hi"] * FPS)
        out.append(
            {
                # yellow triangles still in the window — a "finished" show with any
                # is not finished (Lee 9/1); the page offers Finish again to explode it
                "triangles": sum(1 for o in tri if lo_f <= o < hi_f),
                "lo": p["lo"],
                "hi": p["hi"],
                "lo_hms": hms(p["lo"]),
                "hi_hms": hms(p["hi"]),
                "title": p["title"],
                "language": p.get("language", ""),
                "code": code,
                "source": p.get("source"),
                "ok": r.get("ok", False),
                "state": r.get("state", "cannot" if r.get("error") else "ready"),
                "remainder": r.get("remainder"),
                "finished": bool(r.get("finished")),
                "n_delete": r.get("n_delete", 0),
                "n_insert": r.get("n_insert", 0),
                "overrun": r.get("overrun"),
                "id_airs": r.get("id_airs"),
                "error": r.get("error"),
            }
        )
    return out


def apply_window(
    conn,
    market: int,
    date: str,
    lo: float,
    hi: float,
    apply: bool,
    log=print,
    refill: bool = False,
) -> dict:
    """Plan + write one window. `apply=False` does everything and rolls back.
    `refill`: strip the existing PI/PSA/ID rows first and fill from scratch."""
    cur = conn.cursor()
    lo_f, hi_f = int(lo * FPS), int(hi * FPS)
    # Explode first: the planner reads DURATION, so it must see the conformed rows.
    # Rolled back with everything else if the plan cannot land or apply=False.
    xp = _explode_window(cur, market, date, lo_f, hi_f)
    log(f"  exploded (yellow triangles): {xp['timecodes']} timecodes, {xp['checksums']} checksums")
    r = plan_window(cur, market, date, lo, hi, refill=refill)
    for n in r.get("notes", []):
        log(f"  • {n}")
    if not r["ok"]:
        conn.rollback()
        return {
            **_public(r),
            "status": "cannot",
            "message": r.get("error") or "plan cannot land the ID",
        }

    if r["_id_only"]:
        # nothing to fill — still put the breaks in order (Finish = fill + order)
        try:
            if xp["timecodes"]:
                # conformed DURATIONs move everything behind them by 1-3 frames each
                # (20 spots ≈ 1s — a fixed 0.2s end tolerance rejected every real
                # hour, Lee 9/1). Re-time from the first program piece and prove the
                # hour still FINISHES: the ID airs ≥ ID_MIN_AIR and no content spills.
                _rebuild_shiftup(cur, date, market, next(e for e in r["_evs"] if e.is_program).id)
                after = window_from_day(load_day(cur, market, date), lo, hi)
                ids = [e for e in after if e.newtype == "ID"]
                id_airs = hi - ids[-1].ora if ids else 0.0
                spill = max((e.end for e in after if e.newtype != "ID"), default=lo)
                if id_airs < ID_MIN_AIR or spill > hi + 0.5:
                    raise RuntimeError(
                        f"after explode the ID airs {id_airs:.1f}s and content ends "
                        f"{hms(spill)} — use Refill on this show"
                    )
            bo = _bo_apply(conn, market, date, lo_f, hi_f)
        except Exception as exc:  # noqa: BLE001 — ordering must never break a finished hour
            conn.rollback()
            log(f"ROLLED BACK: break optimization failed: {exc}")
            return {**_public(r), "status": "error", "message": f"break optimization: {exc}"}
        log(
            f"  break optimization: {bo.get('breaks_changed', 0)} of {bo.get('breaks_total', 0)} "
            f"breaks reordered, {bo.get('spots_updated', 0)} spots"
        )
        conn.commit() if apply else conn.rollback()
        log("hour already finished — 0 fill edits")
        return {
            **_public(r),
            "status": "finished",
            "bo": bo,
            "exploded": xp,
            "message": "already finished",
        }

    evs, breaks, deletes, inserts, moves = (
        r["_evs"],
        r["_breaks"],
        r["_deletes"],
        r["_inserts"],
        r["_moves"],
    )
    pieces = [e for e in evs if e.is_program]
    start_of = {}
    t = pieces[0].ora
    for i, p in enumerate(pieces):
        t += p.dur
        b = next((x for x in breaks if x.after_piece_idx == i), None)
        if b:
            for it in b.items:
                start_of[id(it)] = t
                t += it.dur
    planned_end = t

    os.makedirs(RESTORE_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    rpath = os.path.join(
        RESTORE_DIR, f"finish-restore-m{market}-{date}-{int(lo // 3600):02d}-{stamp}.sql"
    )
    with open(rpath, "w") as fh:
        fh.write(f"-- Fill & Finish restore, market {market} {date} {hms(lo)}, {stamp}\n")
        for e in deletes:
            cur.execute("SELECT * FROM trafficPalinse WHERE id_tpalinse=%s", (e.id,))
            cols = [d[0] for d in cur.description]
            for row in cur.fetchall():
                vals = ",".join("NULL" if v is None else f"'{v}'" for v in row)
                fh.write(f"INSERT INTO trafficPalinse ({','.join(cols)}) VALUES ({vals});\n")
            fh.write(f"UPDATE TPALINSE SET LIVELLO=0 WHERE ID_TPALINSE={e.id};\n")
        fh.write("-- inserted rows are appended after the run; undo them with LIVELLO=666\n")
    log(f"  restore SQL: {rpath}")
    for e in deletes:
        log(f"  DEL  {hms(e.ora)} {e.newtype:4} {e.desc[:40]}  (id {e.id})")
    for e, tgt in moves:
        log(
            f"  MOVE {hms(e.ora)} {e.newtype:4} {e.desc[:40]}  (id {e.id}) → break {tgt} at {hms(start_of[id(e)])}"
        )
    for b, x in inserts:
        log(
            f"  INS  {hms(start_of[id(x)])} {x.kind:4} {x.desc[:40]}  (filmati {x.filmati}) → break {b.after_piece_idx}"
        )

    try:
        for e in deletes:
            cur.execute(
                "UPDATE TPALINSE SET LIVELLO=666 WHERE ID_TPALINSE=%s AND LIVELLO=0", (e.id,)
            )
            if cur.rowcount != 1:
                raise RuntimeError(f"delete of {e.id} touched {cur.rowcount} rows")
            cur.execute("DELETE FROM trafficPalinse WHERE id_tpalinse=%s", (e.id,))

        def _seat(row_id: int, b, x) -> None:
            """Give row_id an XORDER between its planned predecessor in break b and the next live row."""
            prev = None
            for it in b.items:
                if it is x:
                    break
                prev = it
            if isinstance(prev, Ev):
                prev_id = prev.id
            elif isinstance(prev, Filler):
                prev_id = new_ids[-1]
            else:
                prev_id = pieces[b.after_piece_idx].id
            cur.execute("SELECT XORDER FROM TPALINSE WHERE ID_TPALINSE=%s", (prev_id,))
            xo_prev = cur.fetchone()[0]
            cur.execute(
                "SELECT MIN(XORDER) FROM TPALINSE WHERE COD_USER=%s AND DATA=%s AND LIVELLO=0 AND XORDER>%s AND ID_TPALINSE<>%s",
                (market, date, xo_prev, row_id),
            )
            xo_next = cur.fetchone()[0]
            xo = (xo_prev + xo_next) // 2 if xo_next else xo_prev + 1000
            if not (xo_prev < xo < (xo_next or xo + 1)):
                raise RuntimeError(f"no XORDER room between {xo_prev} and {xo_next}")
            cur.execute("UPDATE TPALINSE SET XORDER=%s WHERE ID_TPALINSE=%s", (xo, row_id))

        new_ids: list[int] = []
        binding: dict[int, str] = {}
        for e, tgt in moves:
            _seat(e.id, next(bb for bb in breaks if bb.after_piece_idx == tgt), e)
        # The grid's COMS segments sit at NOMINAL offsets (NYC evening: :14, :25:30,
        # :29) while a real break lands wherever the piece ends (19:07:23) — a ±120s
        # search failed on most blocks (Maija 9/1). The segment only decides which
        # traffic break a filler row is booked under (playout = ORA/XORDER), so the
        # nearest segment anywhere in the show's window is correct.
        coms = _slots(cur, market, date, lo_f, hi_f, "COMS")
        for b, x in inserts:
            brk_start = pieces[b.after_piece_idx].end
            slots = coms or _slots(
                cur,
                market,
                date,
                int((brk_start - 120) * FPS),
                int((brk_start + 120) * FPS),
                "COMS",
            )
            if not slots:
                raise RuntimeError(
                    f"no COMS segment in {hms(lo)}-{hms(hi)} for break {b.after_piece_idx}"
                )
            slot = min(slots, key=lambda s: abs(s["ora"] - brk_start * FPS))
            ora = int(round(start_of[id(x)] * FPS))
            nid = _insert_event(
                cur,
                market,
                date,
                slot["sched"],
                slot["block"],
                slot["seg"],
                ora,
                x.filmati,
                _durata(cur, x.filmati),
            )
            cur.execute("EXEC sch_UpdateSupportAndProperties %s,%s,1", (nid, x.filmati))
            cur.execute(
                "UPDATE TPALINSE SET EVENT_TYPE='T', NOTE='CTV_FINISH' WHERE ID_TPALINSE=%s", (nid,)
            )
            binding[nid] = _supporto(cur, x.filmati)
            cur.execute(
                "DELETE FROM trafficPalinse WHERE id_tpalinse=%s AND ID_ContrattiRighe=0", (nid,)
            )
            _seat(nid, b, x)
            new_ids.append(nid)
        cur.execute(
            "UPDATE TPALINSE SET LIVELLO=666 WHERE COD_USER=%s AND DATA=%s AND LIVELLO=0 AND NEWTYPE='NOOP' AND ORA>=%s AND ORA<%s",
            (market, date, lo_f, hi_f),
        )
        _rebuild_shiftup(cur, date, market, pieces[0].id)
        cur.execute(
            "SELECT COUNT(*) FROM TPALINSE WHERE COD_USER=%s AND DATA=%s AND LIVELLO=0 AND NEWTYPE='NOOP' AND ORA>=%s AND ORA<%s",
            (market, date, lo_f, hi_f),
        )
        if cur.fetchone()[0]:
            raise RuntimeError(
                "rebuild left a live NOOP gap-filler in the hour — plan did not reach the top"
            )
        if new_ids:
            _sync_checksums(cur, new_ids, [])
            for nid, sup in binding.items():
                cur.execute("UPDATE TPALINSE SET SUPPORTO=%s WHERE ID_TPALINSE=%s", (sup, nid))
            ids_csv = ",".join(str(i) for i in new_ids)
            # exact readback: the row must carry precisely prefix+FILE_ID (the old
            # LIKE '%FILE_ID%' test could not see a truncated write as such)
            cur.execute(
                f"SELECT ID_TPALINSE, SUPPORTO FROM TPALINSE WHERE ID_TPALINSE IN ({ids_csv})"
            )
            got = dict(cur.fetchall())
            bad = [(nid, got.get(nid)) for nid, sup in binding.items() if got.get(nid) != sup]
            if bad:
                raise RuntimeError(f"SUPPORTO not bound to FILE_ID: {bad}")
            cur.execute(f"SELECT COUNT(*) FROM trafficPalinse WHERE id_tpalinse IN ({ids_csv})")
            if cur.fetchone()[0]:
                raise RuntimeError("inserted rows still carry a trafficPalinse row")
        after = window_from_day(load_day(cur, market, date), lo, hi)
        after_by_id = {e.id: e for e in after}
        for e, tgt in moves:
            a = after_by_id.get(e.id)
            if a is None or abs(a.ora - start_of[id(e)]) > 0.2:
                raise RuntimeError(f"moved row {e.id} did not land at {hms(start_of[id(e)])}")
        end = max(e.end for e in after)
        after_rows = [
            {
                **_item_dict(
                    e,
                    e.ora,
                    "new"
                    if e.id in new_ids
                    else ("pgm" if e.is_program else ("keep" if e.is_fill else "paid")),
                )
            }
            for e in after
        ]
        log(f"  ends {hms(end)}  planned {hms(planned_end)}")
        if abs(end - planned_end) > 0.2:
            raise RuntimeError("packed end does not match the plan")
        # Break Optimization on the touched window (Lee 8/28: "the PI and PSA are out
        # of order" after Finish) — same transaction, so a BO failure rolls Finish back.
        bo = _bo_apply(conn, market, date, lo_f, hi_f)
        log(
            f"  break optimization: {bo.get('breaks_changed', 0)} of {bo.get('breaks_total', 0)} "
            f"breaks reordered, {bo.get('spots_updated', 0)} spots"
        )
        after = window_from_day(load_day(cur, market, date), lo, hi)
        end = max(e.end for e in after)
        after_rows = [
            _item_dict(
                e,
                e.ora,
                "new"
                if e.id in new_ids
                else ("pgm" if e.is_program else ("keep" if e.is_fill else "paid")),
            )
            for e in after
        ]
        if abs(end - planned_end) > 0.2:
            raise RuntimeError("packed end changed after break optimization")
        if not apply:
            conn.rollback()
            log("DRY RUN — rolled back")
            return {
                **_public(r),
                "status": "dry-run",
                "exploded": xp,
                "after": after_rows,
                "end_hms": hms(end),
                "restore": rpath,
                "bo": bo,
            }
        conn.commit()
        with open(rpath, "a") as fh:
            for nid in new_ids:
                fh.write(f"UPDATE TPALINSE SET LIVELLO=666 WHERE ID_TPALINSE={nid};\n")
        log(f"COMMITTED  new rows {new_ids}; restore: {rpath}")
        return {
            **_public(r),
            "status": "applied",
            "exploded": xp,
            "after": after_rows,
            "end_hms": hms(end),
            "new_ids": new_ids,
            "restore": rpath,
            "bo": bo,
        }
    except Exception as exc:  # noqa: BLE001 — anything wrong → the hour is untouched
        conn.rollback()
        log(f"ROLLED BACK: {exc}")
        return {**_public(r), "status": "error", "message": str(exc), "restore": rpath}


def _bo_apply(conn, market: int, date: str, lo_f: int, hi_f: int) -> dict:
    """Break Optimization's own reorder for one window (helpers live in web.routes.orders)."""
    import sys
    from pathlib import Path

    src = str(Path(__file__).resolve().parents[2])
    if src not in sys.path:
        sys.path.insert(0, src)
    from web.routes.orders import bo_apply_market

    return bo_apply_market(conn, market, date, lo_f, hi_f)


def _public(r: dict) -> dict:
    return {k: v for k, v in r.items() if not k.startswith("_")}
