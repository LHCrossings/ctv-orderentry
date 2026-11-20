#!/usr/bin/env python3
"""Mark hiatus days on a contract's lines, the way Etere Web's line form does.

    uv run python3 scripts/hiatus_exclude.py --contract 3089 --dates 2026-12-24 2026-12-25 [--apply]
    uv run python3 scripts/hiatus_exclude.py --remove-line 88497 --dates 2026-12-05 [--apply]

Etere Web stores a hiatus day as one row in trf_contractdateexclude (line id, date at
midnight); confirmed 2026-11-20 on line 88497. This writes the same rows for every line of
the contract whose flight and day pattern include the date. Dry run unless --apply.

Refuses a line that still has spots placed on a hiatus day, unless --remove-placed is given:
then only those spots are pulled (both tables, backup in logs/, aired spots refused) and the
line goes back to Ready, so a reschedule makes up only the pulled spots.
The logic lives in browser_automation/hiatus.py (shared with WorldLink entry and the Hiatus card).

Max per day (Lee 11/20: hiatus spots are made up, not dropped): the week that loses days must
still hold its week's spots, so each affected line's daily cap is raised to
ceil(spots per week / days left in that week). The weekly cap is not touched, so the make-ups
stay in the week they were bought in. A cap is never lowered. A marked line that is Scheduled
but short of its ordered spots is set to Ready, so the scheduler places only the missing spots.
The contract's notes get a `Hiatus 12/24 and 12/25` reminder line, once. 3089: Lines 12/13 M-F, 10 a
week, 3 days left in the week of 12/21 -> cap 2 becomes 4; Line 14 M-Su keeps 2.
"""
import argparse
import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from browser_automation.hiatus import (  # noqa: E402,F401
    HiatusError,
    apply,
    check,
    hiatus_note,
    needed_daily_cap,
    plan,
)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--contract", type=int)
    ap.add_argument("--remove-line", type=int, help="delete this line's exclusion rows for --dates")
    ap.add_argument("--dates", nargs="+", required=True, help="YYYY-MM-DD ...")
    ap.add_argument("--remove-placed", action="store_true", help="pull the spots placed on the hiatus days")
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
    else:
        p = plan(cur, args.contract, dates)
        for ln in p["lines"]:
            cap = f", max per day {ln['cap_old']} -> {ln['cap_new']}" if ln["cap_new"] > ln["cap_old"] else ""
            print(
                f"  line {ln['line_id']} {ln['market']} status {ln['status']} {ln['description']}: "
                f"{len(ln['to_mark'])} day(s) to mark, {ln['placed']} spot(s) placed on them{cap}"
            )
        try:
            r = apply(conn, args.contract, dates, on_placed="remove" if args.remove_placed else "refuse")
        except HiatusError as exc:
            conn.rollback()
            sys.exit(f"REFUSED: {exc}")
        for lid, desc, monday in r["dead_weeks"]:
            print(f"  NOTE line {lid} {desc}: week of {monday} has no day left; its spots cannot be made up in that week")
        print(f"{r['marked_rows']} exclusion row(s) to add on {r['lines']} line(s)")
        print(f"{len(r['caps'])} line(s) get a higher max per day")
        print(f"{r['removed']} placed spot(s) pulled" + (f" (backup {r['backup']})" if r["backup"] else ""))
        print(f"{r['to_ready']} short line(s) set from Scheduled to Ready")
        print(f"{r['released']} scheduler blacklist row(s) released on marked lines")
        print(f"contract note: {'add ' + repr(r['hiatus_note']) if r['note_added'] else 'unchanged'}")

    if not args.apply:
        conn.rollback()
        print("dry run: nothing written (add --apply)")
        return
    conn.commit()
    conn.close()

    # readback on a fresh connection
    conn = connect()
    if args.remove_line:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM trf_contractdateexclude WHERE id_contrattirighe = %s", (args.remove_line,))
        print(f"readback: line {args.remove_line} now has {cur.fetchone()[0]} exclusion row(s)")
    else:
        c = check(conn, args.contract)
        print(f"readback: exclusions by date: {[(str(d), n) for d, n in c['excluded']]}")
        print(f"readback: ordered {c['ordered']}, placed {c['placed']}, on hiatus days {c['on_hiatus_days']}, "
              f"short marked lines {len(c['short_lines'])}, ghosts {c['ghosts']}")
        print(f"readback: contract note: {c['note']!r}")
    conn.close()


if __name__ == "__main__":
    main()
