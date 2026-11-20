#!/usr/bin/env python3
"""Mark hiatus days on a contract's lines, the way Etere Web's line form does.

    uv run python3 scripts/hiatus_exclude.py --contract 3089 --dates 2026-12-24 2026-12-25 [--apply]
    uv run python3 scripts/hiatus_exclude.py --remove-line 88497 --dates 2026-12-05 [--apply]

Etere Web stores a hiatus day as one row in trf_contractdateexclude (line id, date at
midnight); confirmed 2026-11-20 on line 88497. This writes the same rows for every line of
the contract whose flight and day pattern include the date. Dry run unless --apply.

Refuses a line that still has spots placed on a hiatus day: unschedule first (Change Data on
the line in the Etere app), then mark, then reschedule.

Max per day (Lee 11/20: hiatus spots are made up, not dropped): the week that loses days must
still hold its week's spots, so each affected line's daily cap is raised to
ceil(spots per week / days left in that week). The weekly cap is not touched, so the make-ups
stay in the week they were bought in. A cap is never lowered. A marked line that is Scheduled
but short of its ordered spots is set to Ready, so the scheduler places only the missing spots. 3089: Lines 12/13 M-F, 10 a
week, 3 days left in the week of 12/21 -> cap 2 becomes 4; Line 14 M-Su keeps 2.
"""
import argparse
import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DAY_COLS = ("LUNEDI", "MARTEDI", "MERCOLEDI", "GIOVEDI", "VENERDI", "SABATO", "DOMENICA")


def needed_daily_cap(start, end, day_flags, excluded, total_spots, weekly_spots):
    """(cap the hiatus weeks need, [weeks with no day left]) for one line.

    Weeks run Monday-Sunday. Only weeks that lose a day to `excluded` are looked at.
    weekly_spots 0 means the line has no weekly cap: the flight total is spread over its weeks.
    """
    excluded = {d for d in excluded if start <= d <= end and day_flags[d.weekday()]}
    first_monday = start - dt.timedelta(days=start.weekday())
    weeks = (end - first_monday).days // 7 + 1
    per_week = weekly_spots or -(-total_spots // weeks)
    cap, dead = 0, []
    for monday in sorted({d - dt.timedelta(days=d.weekday()) for d in excluded}):
        days = [monday + dt.timedelta(days=i) for i in range(7)]
        left = [d for d in days if start <= d <= end and day_flags[d.weekday()] and d not in excluded]
        if not left:
            dead.append(monday)
            continue
        cap = max(cap, -(-per_week // len(left)))
    return cap, dead


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
                       N_PASSAGGI, PASSAGGI_GIORNALIERI, PASSAGGI_SETTIMANALI, {", ".join(DAY_COLS)}
                FROM CONTRATTIRIGHE WHERE ID_CONTRATTITESTATA = %s ORDER BY ID_CONTRATTIRIGHE""",
            (args.contract,),
        )
        lines = cur.fetchall()
        if not lines:
            sys.exit(f"contract {args.contract}: no lines")
        todo, blocked, caps = [], [], []
        for lid, desc, mkt, d0, d1, status, n_tot, cap_day, cap_week, *days in lines:
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
            cur.execute("SELECT dateexclude FROM trf_contractdateexclude WHERE id_contrattirighe = %s", (lid,))
            excl = {r[0].date() for r in cur.fetchall()} | {d for ln, d in todo if ln == lid}
            need, dead = needed_daily_cap(d0.date(), d1.date(), days, excl, n_tot, cap_week)
            for monday in dead:
                print(f"  NOTE line {lid} {desc}: week of {monday} has no day left; its spots cannot be made up in that week")
            if need > (cap_day or 0):
                caps.append((lid, cap_day, need))
                print(f"  line {lid} mkt {mkt} {desc}: max per day {cap_day} -> {need}")
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
        for lid, _, need in caps:
            cur.execute("UPDATE CONTRATTIRIGHE SET PASSAGGI_GIORNALIERI = %s WHERE ID_CONTRATTIRIGHE = %s", (need, lid))
        print(f"{len(caps)} line(s) get a higher max per day")
        # A marked line that is Scheduled (1) but short of its ordered spots is invisible to the
        # scheduler; Ready (0) brings it back so only the missing spots are placed. Placed spots
        # stay put (Lee 11/20: Change Data would unschedule the whole line, which is not wanted).
        cur.execute(
            """UPDATE cr SET ROWSTATUS = 0 FROM CONTRATTIRIGHE cr
               WHERE cr.ID_CONTRATTITESTATA = %s AND cr.ROWSTATUS = 1
                 AND (EXISTS (SELECT 1 FROM trf_contractdateexclude e WHERE e.id_contrattirighe = cr.ID_CONTRATTIRIGHE)
                      OR cr.ID_CONTRATTIRIGHE IN ({ids}))
                 AND cr.N_PASSAGGI > (SELECT COUNT(*) FROM trafficPalinse t
                                      WHERE t.ID_ContrattiRighe = cr.ID_CONTRATTIRIGHE)""".format(
                ids=",".join(str(lid) for lid, _ in todo) or "0"
            ),
            (args.contract,),
        )
        print(f"{cur.rowcount} short line(s) set from Scheduled to Ready")
        # When a reschedule ran before the cap was raised, the scheduler parked the spots it could
        # not place on the blacklist ("Inserted from Server ..."); a line shows as blacklist-only
        # until those rows go (3089, 11/20). Only the scheduler's own rows on marked lines.
        cur.execute(
            """DELETE l FROM Traffic_ScheduleList l JOIN CONTRATTIRIGHE cr ON cr.ID_CONTRATTIRIGHE = l.ID_ContrattiRighe
               WHERE cr.ID_CONTRATTITESTATA = %s AND l.BlackList > 0 AND l.Notes LIKE 'Inserted from Server%%'
                 AND EXISTS (SELECT 1 FROM trf_contractdateexclude e WHERE e.id_contrattirighe = cr.ID_CONTRATTIRIGHE)""",
            (args.contract,),
        )
        print(f"{cur.rowcount} scheduler blacklist row(s) released on marked lines")
        expected_rows = len(todo)
        print(f"{len(todo)} row(s) to add on {len({ln for ln, _ in todo})} line(s)")

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
        cur.execute(
            """SELECT PASSAGGI_GIORNALIERI, COUNT(*) FROM CONTRATTIRIGHE cr WHERE cr.ID_CONTRATTITESTATA = %s
               AND EXISTS (SELECT 1 FROM trf_contractdateexclude e WHERE e.id_contrattirighe = cr.ID_CONTRATTIRIGHE)
               GROUP BY PASSAGGI_GIORNALIERI ORDER BY 1""",
            (args.contract,),
        )
        print(f"readback: max per day on the marked lines (cap, lines): {cur.fetchall()}")
    conn.close()


if __name__ == "__main__":
    main()
