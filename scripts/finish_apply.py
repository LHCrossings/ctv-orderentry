"""Fill & Finish — APPLY one hour's plan (spec: tasks/finish-hour.md v0.3).

Computes the plan with finish_plan.plan(), then in ONE transaction:
  deletes  → LIVELLO=666 + trafficPalinse row removed (Etere's own convention)
  inserts  → Traffic_InsertEvent into the break's COMS segment (the FCC daily-ID
             recipe: sch_UpdateSupportAndProperties, EVENT_TYPE='T', checksum sync)
             + XORDER placed between its neighbours, NOTE='CTV_FINISH' ownership tag
  rebuild  → sch_rebuildStartTimeSchedule from the first touched row
  verify   → reload the window; the packed end must match the plan or ROLLBACK
Restore SQL is written to the scratchpad before anything is touched.

    uv run python3 scripts/finish_apply.py --market 6 --date 2026-08-28 --hour 8          # dry run (rollback)
    uv run python3 scripts/finish_apply.py --market 6 --date 2026-08-28 --hour 8 --apply
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

sys.path.insert(0, ".")
from browser_automation.etere_direct_client import connect  # noqa: E402
from scripts.finish_plan import (  # noqa: E402
    FPS,
    Ev,
    Filler,
    hms,
    load_inventory,
    load_window,
    plan,
)
from src.business_logic.services.daily_programming_run import (  # noqa: E402
    _durata,
    _insert_event,
    _rebuild,
    _slots,
    _sync_checksums,
)

SCRATCH = os.environ.get(
    "CLAUDE_SCRATCHPAD",
    "/tmp/claude-1000/-home-scrib-dev-ctv-orderentry/0411d39e-68df-4801-b73a-a7c6e5ab8ca2/scratchpad",
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", type=int, required=True)
    ap.add_argument("--date", required=True)
    ap.add_argument("--hour", type=int, required=True)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    lo, hi = a.hour * 3600.0, (a.hour + 1) * 3600.0
    conn = connect()
    cur = conn.cursor()
    evs = load_window(cur, a.market, a.date, lo, hi)
    inv = load_inventory(cur, a.market, a.date)
    breaks, notes = plan(evs, inv, hi, a.market)
    for n in notes:
        print("  •", n)
    if any(n.startswith("⚠") for n in notes):
        print("plan cannot land the ID — nothing done")
        return 1

    planned_items = {id(x) for b in breaks for x in b.items}
    deletes = [e for e in evs if e.newtype != "ID" and e.is_fill and id(e) not in planned_items]
    old_ids = [e for e in evs if e.newtype == "ID"]
    inserts = [(b, x) for b in breaks for x in b.items if isinstance(x, Filler)]
    # an existing ID that the plan re-places with the same asset in the same spot is a no-op
    if old_ids and len(inserts) == 1 and inserts[0][1].kind == "ID" and not deletes:
        print("hour already finished — 0 edits")
        return 0
    deletes += old_ids  # any other case: the old ID is re-placed by the new one
    if not deletes and not inserts:
        print("nothing to do")
        return 0

    pieces = [e for e in evs if e.is_program]
    # planned start of every item, from the packed timeline
    t = pieces[0].ora
    start_of: dict[int, float] = {}
    for i, p in enumerate(pieces):
        t += p.dur
        b = next((x for x in breaks if x.after_piece_idx == i), None)
        if b:
            for it in b.items:
                start_of[id(it)] = t
                t += it.dur
    planned_end = t

    # restore SQL first
    os.makedirs(SCRATCH, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    rpath = f"{SCRATCH}/finish-restore-m{a.market}-{a.date}-h{a.hour}-{stamp}.sql"
    with open(rpath, "w") as fh:
        fh.write(f"-- Fill & Finish restore, market {a.market} {a.date} {a.hour:02d}:00, {stamp}\n")
        for e in deletes:
            cur.execute("SELECT * FROM trafficPalinse WHERE id_tpalinse=%s", (e.id,))
            cols = [d[0] for d in cur.description]
            for r in cur.fetchall():
                vals = ",".join("NULL" if v is None else f"'{v}'" for v in r)
                fh.write(f"INSERT INTO trafficPalinse ({','.join(cols)}) VALUES ({vals});\n")
            fh.write(f"UPDATE TPALINSE SET LIVELLO=0 WHERE ID_TPALINSE={e.id};\n")
        fh.write("-- inserted rows are listed below after the run; delete them with LIVELLO=666\n")
    print(f"  restore SQL: {rpath}")

    print("\nEDITS")
    for e in deletes:
        print(f"  DEL  {hms(e.ora)} {e.newtype:4} {e.desc[:40]}  (id {e.id})")
    for b, x in inserts:
        print(
            f"  INS  {hms(start_of[id(x)])} {x.kind:4} {x.desc[:40]}  (filmati {x.filmati}) → break {b.after_piece_idx}"
        )

    try:
        for e in deletes:
            cur.execute(
                "UPDATE TPALINSE SET LIVELLO=666 WHERE ID_TPALINSE=%s AND LIVELLO=0", (e.id,)
            )
            assert cur.rowcount == 1, f"delete of {e.id} touched {cur.rowcount} rows"
            cur.execute("DELETE FROM trafficPalinse WHERE id_tpalinse=%s", (e.id,))
        new_ids = []
        for b, x in inserts:
            brk_start = pieces[b.after_piece_idx].end
            slots = _slots(
                cur,
                a.market,
                a.date,
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
                a.market,
                a.date,
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
            # XORDER: between the previous planned item in this break (or the piece) and the next live row
            prev = None
            for it in b.items:
                if it is x:
                    break
                prev = it
            prev_id = (
                prev.id
                if isinstance(prev, Ev)
                else (new_ids[-1] if prev is not None and new_ids else pieces[b.after_piece_idx].id)
            )
            if isinstance(prev, Filler):
                prev_id = new_ids[-1]
            cur.execute("SELECT XORDER FROM TPALINSE WHERE ID_TPALINSE=%s", (prev_id,))
            xo_prev = cur.fetchone()[0]
            cur.execute(
                "SELECT MIN(XORDER) FROM TPALINSE WHERE COD_USER=%s AND DATA=%s AND LIVELLO=0 AND XORDER>%s AND ID_TPALINSE<>%s",
                (a.market, a.date, xo_prev, nid),
            )
            xo_next = cur.fetchone()[0]
            xo = (xo_prev + xo_next) // 2 if xo_next else xo_prev + 1000
            assert xo_prev < xo < (xo_next or xo + 1), (
                f"no XORDER room between {xo_prev} and {xo_next}"
            )
            cur.execute("UPDATE TPALINSE SET XORDER=%s WHERE ID_TPALINSE=%s", (xo, nid))
            new_ids.append(nid)
        # rebuild the rest of the day from the hour's first piece (open bump)
        _rebuild(cur, a.date, a.market, pieces[0].id)
        if new_ids:
            _sync_checksums(cur, new_ids, [])
        # verify
        after = load_window(cur, a.market, a.date, lo, hi)
        end = after[-1].end
        print("\nAFTER")
        for e in after:
            if e.ora >= pieces[-1].ora - 0.5:
                print(
                    f"  {hms(e.ora)} {e.newtype:4} {e.desc[:40]}{'  [new]' if e.id in new_ids else ''}"
                )
        print(f"  ends {hms(end)}  planned {hms(planned_end)}")
        if abs(end - planned_end) > 0.2:
            raise RuntimeError("packed end does not match the plan")
        if not a.apply:
            conn.rollback()
            print("\nDRY RUN — rolled back")
            return 0
        conn.commit()
        with open(rpath, "a") as fh:
            for nid in new_ids:
                fh.write(f"UPDATE TPALINSE SET LIVELLO=666 WHERE ID_TPALINSE={nid};\n")
        print(f"\nCOMMITTED  new rows {new_ids}; restore: {rpath}")
        return 0
    except Exception as exc:
        conn.rollback()
        print(f"\nROLLED BACK: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
