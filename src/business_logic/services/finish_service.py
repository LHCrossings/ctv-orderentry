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
    Ev,
    Filler,
    hms,
    load_day,
    load_inventory,
    plan,
    window_from_day,
)

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


def _refresh_checksums(cur, market: int, date: str, lo_f: int, hi_f: int) -> int:
    """Clear yellow triangles in the hour (Lee 8/28): a piece dragged in before its
    file reached the CIBs keeps a pre-download SCHEDULE_CHECKSUM; once the file
    settles, stored != live and EE shows the triangle until someone Explodes it.
    Storing the live value IS Explode. Touches TPALINSE only — never FILMATI."""
    cur.execute(
        """UPDATE TPALINSE SET SCHEDULE_CHECKSUM = dbo.sch_getFilmatiCheckSum(ID_TPALINSE)
           WHERE COD_USER=%s AND DATA=%s AND LIVELLO=0 AND ORA>=%s AND ORA<%s
             AND ISNULL(SCHEDULE_CHECKSUM,0) <> ISNULL(dbo.sch_getFilmatiCheckSum(ID_TPALINSE),0)""",
        (market, date, lo_f, hi_f),
    )
    return cur.rowcount


def _supporto(cur, filmati: int) -> str:
    """Playout binding = channel prefix + FS_FILMATI.FILE_ID (same rule as
    orders._pi_filler_supporto)."""
    cur.execute(
        "SELECT TOP 1 ISNULL(d.LEGACY_BASESUPP, CAST(d.LEGACY_MEDIAID AS VARCHAR) + 'ETX      '), ff.FILE_ID"
        " FROM FS_FILMATI ff JOIN FS_METADEVICE d ON d.ID_METADEVICE = ff.ID_METADEVICE"
        " WHERE ff.ID_FILMATI = %s AND d.LEGACY_MEDIAID IS NOT NULL ORDER BY d.LEGACY_MEDIAID",
        (int(filmati),),
    )
    r = cur.fetchone()
    if not r or not r[1]:
        raise RuntimeError(f"no FS_FILMATI FILE_ID for {filmati}")
    return (str(r[0]) + str(r[1]))[:30]


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


def plan_window(cur, market: int, date: str, lo: float, hi: float, rows=None) -> dict:
    """Read-only: the packed timeline Finish would produce, plus the edit list.
    `finished` is DERIVED: nothing to delete/insert except re-placing the ID that is
    already there (planner reports 0 edits + ID present)."""
    rows = rows if rows is not None else load_day(cur, market, date)
    evs = window_from_day(rows, lo, hi)
    if not evs or not any(e.is_program for e in evs):
        return {"ok": False, "error": "no program pieces in window", "timeline": [], "edits": []}
    inv = load_inventory(cur, market, date)
    breaks, notes = plan(evs, inv, hi, market)
    planned = {id(x) for b in breaks for x in b.items}
    deletes = [e for e in evs if e.newtype != "ID" and e.is_fill and id(e) not in planned]
    old_ids = [e for e in evs if e.newtype == "ID"]
    inserts = [(b, x) for b in breaks for x in b.items if isinstance(x, Filler)]
    id_only = bool(old_ids) and len(inserts) == 1 and inserts[0][1].kind == "ID" and not deletes
    cannot = any(n.startswith("⚠") for n in notes)
    finished = id_only and not cannot
    if not id_only:
        deletes = deletes + old_ids

    pieces = [e for e in evs if e.is_program]
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
                tag = "keep" if it.is_fill else "paid"
            timeline.append({**_item_dict(it, t, tag), "break": i})
            t += it.dur
        timeline[-1]["break_len"] = t - b_start
        timeline[-1]["break_end"] = True
    for e in deletes:
        edits.insert(0, {"op": "delete", **_item_dict(e, e.ora, "del")})
    actual_end = max(e.end for e in evs)
    rem_s = hi - pieces[0].ora - sum(e.dur for e in evs if e.newtype not in ("ID", "NOOP"))
    if len(pieces) == 1 and not breaks[0].items if breaks else True:
        state = (
            "na"  # a single fixed event with no breaks (overnight live feed) — nothing to finish
        )
    elif rem_s > UNPLACED_SECONDS:
        state = "unplaced"  # the precondition (all programming placed) is not met
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
        "finished": finished if not id_only else True,
        "notes": notes,
        "timeline": timeline,
        "edits": edits if not id_only else [],
        "planned_end": t,
        "planned_end_hms": hms(t),
        "actual_end": actual_end,
        "actual_end_hms": hms(actual_end),
        "window_end": hi,
        "overrun": actual_end - hi,
        "id_airs": id_airs,
        "n_delete": len(deletes) if not id_only else 0,
        "n_insert": len(inserts) if not id_only else 0,
        "_breaks": breaks,
        "_evs": evs,
        "_deletes": deletes if not id_only else [],
        "_inserts": inserts if not id_only else [],
        "_id_only": id_only,
    }


def list_programs(cur, market: int, date: str) -> list[dict]:
    """The day's program windows (F→F) with the derived Finish state for each."""
    from src.business_logic.services.finish_plan import day_programs

    rows = load_day(cur, market, date)
    out = []
    for p in day_programs(rows):
        r = plan_window(cur, market, date, p["lo"], p["hi"], rows=rows)
        out.append(
            {
                "lo": p["lo"],
                "hi": p["hi"],
                "lo_hms": hms(p["lo"]),
                "hi_hms": hms(p["hi"]),
                "title": p["title"],
                "n_events": p["n_events"],
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
    conn, market: int, date: str, lo: float, hi: float, apply: bool, log=print
) -> dict:
    """Plan + write one window. `apply=False` does everything and rolls back."""
    cur = conn.cursor()
    lo_f, hi_f = int(lo * FPS), int(hi * FPS)
    r = plan_window(cur, market, date, lo, hi)
    for n in r.get("notes", []):
        log(f"  • {n}")
    if not r["ok"]:
        conn.rollback()
        return {
            **_public(r),
            "status": "cannot",
            "message": r.get("error") or "plan cannot land the ID",
        }

    n_ck = _refresh_checksums(cur, market, date, lo_f, hi_f)
    log(f"  checksums refreshed (yellow triangles): {n_ck}")
    if r["_id_only"] or (not r["_deletes"] and not r["_inserts"]):
        conn.commit() if apply else conn.rollback()
        log("hour already finished — 0 edits")
        return {
            **_public(r),
            "status": "finished",
            "checksums": n_ck,
            "message": "already finished",
        }

    evs, breaks, deletes, inserts = r["_evs"], r["_breaks"], r["_deletes"], r["_inserts"]
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
        new_ids: list[int] = []
        binding: dict[int, str] = {}
        for b, x in inserts:
            brk_start = pieces[b.after_piece_idx].end
            slots = _slots(
                cur,
                market,
                date,
                int((brk_start - 120) * FPS),
                int((brk_start + 120) * FPS),
                "COMS",
            )
            if not slots:
                raise RuntimeError(
                    f"no COMS segment near {hms(brk_start)} for break {b.after_piece_idx}"
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
                (market, date, xo_prev, nid),
            )
            xo_next = cur.fetchone()[0]
            xo = (xo_prev + xo_next) // 2 if xo_next else xo_prev + 1000
            if not (xo_prev < xo < (xo_next or xo + 1)):
                raise RuntimeError(f"no XORDER room between {xo_prev} and {xo_next}")
            cur.execute("UPDATE TPALINSE SET XORDER=%s WHERE ID_TPALINSE=%s", (xo, nid))
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
            cur.execute(
                f"""SELECT t.ID_TPALINSE, t.SUPPORTO FROM TPALINSE t
                    WHERE t.ID_TPALINSE IN ({ids_csv})
                      AND NOT EXISTS (SELECT 1 FROM FS_FILMATI ff WHERE ff.ID_FILMATI=t.ID_FILMATI
                                      AND t.SUPPORTO LIKE '%' + ff.FILE_ID + '%')"""
            )
            bad = cur.fetchall()
            if bad:
                raise RuntimeError(f"SUPPORTO not bound to FILE_ID: {bad}")
            cur.execute(f"SELECT COUNT(*) FROM trafficPalinse WHERE id_tpalinse IN ({ids_csv})")
            if cur.fetchone()[0]:
                raise RuntimeError("inserted rows still carry a trafficPalinse row")
        after = window_from_day(load_day(cur, market, date), lo, hi)
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
        if not apply:
            conn.rollback()
            log("DRY RUN — rolled back")
            return {
                **_public(r),
                "status": "dry-run",
                "checksums": n_ck,
                "after": after_rows,
                "end_hms": hms(end),
                "restore": rpath,
            }
        conn.commit()
        with open(rpath, "a") as fh:
            for nid in new_ids:
                fh.write(f"UPDATE TPALINSE SET LIVELLO=666 WHERE ID_TPALINSE={nid};\n")
        log(f"COMMITTED  new rows {new_ids}; restore: {rpath}")
        return {
            **_public(r),
            "status": "applied",
            "checksums": n_ck,
            "after": after_rows,
            "end_hms": hms(end),
            "new_ids": new_ids,
            "restore": rpath,
        }
    except Exception as exc:  # noqa: BLE001 — anything wrong → the hour is untouched
        conn.rollback()
        log(f"ROLLED BACK: {exc}")
        return {**_public(r), "status": "error", "message": str(exc), "restore": rpath}


def _public(r: dict) -> dict:
    return {k: v for k, v in r.items() if not k.startswith("_")}
