#!/usr/bin/env python3
"""Mark hiatus days on a contract's lines, the way Etere Web's line form does.

    uv run python3 scripts/hiatus_exclude.py --contract 3089 --dates 2026-12-24 2026-12-25 [--apply]
    uv run python3 scripts/hiatus_exclude.py --remove-line 88497 --dates 2026-12-05 [--apply]

Etere Web stores a hiatus day as one row in trf_contractdateexclude (line id, date at
midnight); confirmed 2026-11-20 on line 88497. This writes the same rows for every line of
the contract whose flight and day pattern include the date. Dry run unless --apply.

Refuses a line that still has spots placed on a hiatus day: unschedule first (Change Data on
the line in the Etere app), then mark, then reschedule.
"""
import argparse
import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DAY_COLS = ("LUNEDI", "MARTEDI", "MERCOLEDI", "GIOVEDI", "VENERDI", "SABATO", "DOMENICA")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--contract", type=int)
    ap.add_argument("--remove-line", type=int, help="delete this line's exclusion rows for --dates")
    ap.add_argument("--dates", nargs="+", required=True, help="YYYY-MM-DD ...")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    dates = [dt.date.fromisoformat(d) for d in args.dates]
    if bool(args.contract) == bool(args.remove_line):
        ap.error("give --contract or --remove-line")

    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from browser_automation.etere_direct_client import connect

    conn = connect()
    cur = conn.cursor()
    if args.remove_line:
        for d in dates:
            cur.execute(
                "DELETE FROM trf_contractdateexclude WHERE id_contrattirighe = %s AND dateexclude = %s",
                (args.remove_line, d.isoformat()),
            )
            print(f"line {args.remove_line} {d}: {cur.rowcount} row(s) removed")
        expected_rows = None
    else:
        cur.execute(
            f"""SELECT ID_CONTRATTIRIGHE, RTRIM(DESCRIZIONE), COD_USER, DATESTART, DATEEND, ROWSTATUS,
                       {", ".join(DAY_COLS)}
                FROM CONTRATTIRIGHE WHERE ID_CONTRATTITESTATA = %s ORDER BY ID_CONTRATTIRIGHE""",
            (args.contract,),
        )
        lines = cur.fetchall()
        if not lines:
            sys.exit(f"contract {args.contract}: no lines")
        todo, blocked = [], []
        for lid, desc, mkt, d0, d1, status, *days in lines:
            for d in dates:
                if not (d0.date() <= d <= d1.date()) or not days[d.weekday()]:
                    continue
                cur.execute(
                    """SELECT COUNT(*) FROM trafficPalinse t JOIN TPALINSE p ON p.ID_TPALINSE = t.id_tpalinse
                       WHERE t.ID_ContrattiRighe = %s AND CAST(p.DATA AS DATE) = %s""",
                    (lid, d.isoformat()),
                )
                if cur.fetchone()[0]:
                    blocked.append((lid, desc, d))
                cur.execute(
                    "SELECT COUNT(*) FROM trf_contractdateexclude WHERE id_contrattirighe = %s AND dateexclude = %s",
                    (lid, d.isoformat()),
                )
                have = cur.fetchone()[0]
                print(f"  line {lid} mkt {mkt} status {status} {desc}: {d} {'already marked' if have else 'to mark'}")
                if not have:
                    todo.append((lid, d))
        if blocked:
            for lid, desc, d in blocked:
                print(f"REFUSED: line {lid} {desc} still has spots placed on {d}")
            conn.rollback()
            sys.exit(2)
        for lid, d in todo:
            cur.execute(
                "INSERT INTO trf_contractdateexclude (id_contrattirighe, dateexclude) VALUES (%s, %s)",
                (lid, d.isoformat()),
            )
        expected_rows = len(todo)
        print(f"{len(todo)} row(s) to add on {len({l for l, _ in todo})} line(s)")

    if not args.apply:
        conn.rollback()
        print("dry run: nothing written (add --apply)")
        return
    conn.commit()
    conn.close()

    # readback on a fresh connection
    conn = connect()
    cur = conn.cursor()
    if args.remove_line:
        cur.execute("SELECT COUNT(*) FROM trf_contractdateexclude WHERE id_contrattirighe = %s", (args.remove_line,))
        print(f"readback: line {args.remove_line} now has {cur.fetchone()[0]} exclusion row(s)")
    else:
        cur.execute(
            """SELECT CAST(e.dateexclude AS DATE), COUNT(*) FROM trf_contractdateexclude e
               JOIN CONTRATTIRIGHE cr ON cr.ID_CONTRATTIRIGHE = e.id_contrattirighe
               WHERE cr.ID_CONTRATTITESTATA = %s GROUP BY CAST(e.dateexclude AS DATE) ORDER BY 1""",
            (args.contract,),
        )
        print(f"readback: contract {args.contract} exclusions by date: {[(str(d), n) for d, n in cur.fetchall()]}"
              f" ({expected_rows} added this run)")
    conn.close()


if __name__ == "__main__":
    main()
