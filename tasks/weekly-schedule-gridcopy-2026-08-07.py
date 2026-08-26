"""Rebuild the WDC + MMT program grid forward, the way Etere's own copy does.

Etere's Copy Between Stations refuses with a bogus "days with a duration greater than
24 hours" for these two stations, naming the same 7 dates regardless of the source or
destination week chosen. The stored grid is provably fine (WDC's week of 12/28 is
block-for-block identical to CVC's, which copies happily), so this does the copy
directly instead.

What Etere's copy produces, confirmed by diffing CVC 12/28/26 against CVC 1/4/27:
  * a NEW traffic_schedule row per day, carrying the source's Name/Flag/Notes/
    Insert_Date verbatim (that is why live grids are labelled with 2021 dates);
  * traffic_scheduleblock rows duplicated with the SAME ID_TrafficBlock and Offset
    (blocks are shared station assets, never copied);
  * a Traffic_Calendar row at Level 0 for the new date.
Nothing else: Traffic_DayStructure* are VIEWS, and trf_priceblock is empty database-wide.

Safe because WDC and MMT hold zero placed spots (TPALINSE + trafficPalinse) from
12/28/26 onward, so no scheduling depends on the current grid.

Dry run performs every insert and verification then ROLLS BACK. --execute commits.
"""

import sys
from datetime import date, timedelta

sys.path.insert(0, ".")
from browser_automation.etere_direct_client import connect

EXECUTE = "--execute" in sys.argv

JOBS = [
    # (cod_user, label, source week Monday, first target, last target)
    (8, "WDC", date(2026, 12, 28), date(2027, 1, 4), date(2027, 6, 27)),
    (9, "MMT", date(2026, 12, 21), date(2026, 12, 28), date(2027, 6, 27)),
]

SPAN_24H = 2589408  # 24h at 29.97fps; broadcast day 06:00 -> 30:00
DAY_START = 647352  # 06:00:00


def main():
    conn = connect()
    cur = conn.cursor()
    ok = True
    try:
        for cu, label, src_mon, first, last in JOBS:
            print(
                f"\n{'=' * 66}\n{label}: source week {src_mon} -> targets {first} .. {last}\n{'=' * 66}"
            )

            # ---- load the 7 source days ----
            src = {}
            for i in range(7):
                d = src_mon + timedelta(days=i)
                cur.execute(
                    """SELECT ca.ID_TrafficSchedule, ca.Notes, ca.JingleInserted,
                                      ca.blockautoinsert, ca.blockautoinsertUser
                               FROM Traffic_Calendar ca WITH(NOLOCK)
                               WHERE ca.Cod_User=%s AND ca.Date=%s AND ca.Level=0""",
                    (cu, d),
                )
                row = cur.fetchone()
                if not row:
                    print(f"  ABORT: source day {d} missing")
                    return False
                cur.execute(
                    """SELECT COUNT(*) FROM traffic_scheduleblock WITH(NOLOCK)
                               WHERE ID_TrafficSchedule=%s""",
                    (row[0],),
                )
                src[i] = {"sched": row[0], "cal": row[1:], "nblk": cur.fetchone()[0]}
                print(f"  source {d} ({d.strftime('%a')}): sched={row[0]} blocks={src[i]['nblk']}")

            # ---- guard: targets must be empty ----
            cur.execute(
                """SELECT COUNT(*) FROM Traffic_Calendar WITH(NOLOCK)
                           WHERE Cod_User=%s AND Date BETWEEN %s AND %s""",
                (cu, first, last),
            )
            n = cur.fetchone()[0]
            if n:
                print(f"  ABORT: {n} calendar row(s) already exist in the target range")
                return False

            # ---- copy ----
            targets = []
            d = first
            while d <= last:
                targets.append(d)
                d += timedelta(days=1)
            print(f"  copying {len(targets)} day(s)...")

            for d in targets:
                s = src[d.weekday()]  # Monday=0, aligns with both source weeks
                cur.execute(
                    """INSERT INTO traffic_schedule (Name,Flag,Notes,Cod_User,Insert_Date,Expired)
                               OUTPUT INSERTED.ID_TrafficSchedule
                               SELECT Name,Flag,Notes,Cod_User,Insert_Date,Expired
                               FROM traffic_schedule WHERE ID_TrafficSchedule=%s""",
                    (s["sched"],),
                )
                new_sched = cur.fetchone()[0]
                cur.execute(
                    """INSERT INTO traffic_scheduleblock (ID_TrafficSchedule,ID_TrafficBlock,Locked,Offset)
                               SELECT %s, ID_TrafficBlock, Locked, Offset
                               FROM traffic_scheduleblock WHERE ID_TrafficSchedule=%s""",
                    (new_sched, s["sched"]),
                )
                notes, jingle, bai, baiu = s["cal"]
                cur.execute(
                    """INSERT INTO Traffic_Calendar
                                 (Date,Cod_User,Level,ID_TrafficSchedule,Notes,
                                  JingleInserted,blockautoinsert,blockautoinsertUser)
                               VALUES (%s,%s,0,%s,%s,%s,%s,%s)""",
                    (d, cu, new_sched, notes, jingle, bai, baiu),
                )

            # ---- verify every day against its source ----
            bad = []
            for d in targets:
                s = src[d.weekday()]
                cur.execute(
                    """SELECT
                     (SELECT COUNT(*) FROM (
                        SELECT sb.ID_TrafficBlock, sb.Offset FROM traffic_scheduleblock sb
                          WHERE sb.ID_TrafficSchedule=%s
                        EXCEPT
                        SELECT sb.ID_TrafficBlock, sb.Offset FROM Traffic_Calendar ca
                          JOIN traffic_scheduleblock sb ON sb.ID_TrafficSchedule=ca.ID_TrafficSchedule
                          WHERE ca.Cod_User=%s AND ca.Date=%s) x),
                     (SELECT COUNT(*) FROM (
                        SELECT sb.ID_TrafficBlock, sb.Offset FROM Traffic_Calendar ca
                          JOIN traffic_scheduleblock sb ON sb.ID_TrafficSchedule=ca.ID_TrafficSchedule
                          WHERE ca.Cod_User=%s AND ca.Date=%s
                        EXCEPT
                        SELECT sb.ID_TrafficBlock, sb.Offset FROM traffic_scheduleblock sb
                          WHERE sb.ID_TrafficSchedule=%s) y),
                     (SELECT COUNT(*) FROM Traffic_Calendar ca
                        JOIN traffic_scheduleblock sb ON sb.ID_TrafficSchedule=ca.ID_TrafficSchedule
                        WHERE ca.Cod_User=%s AND ca.Date=%s),
                     (SELECT MIN(sb.Offset) FROM Traffic_Calendar ca
                        JOIN traffic_scheduleblock sb ON sb.ID_TrafficSchedule=ca.ID_TrafficSchedule
                        WHERE ca.Cod_User=%s AND ca.Date=%s),
                     (SELECT MAX(CAST(sb.Offset AS bigint)+bl.Duration) FROM Traffic_Calendar ca
                        JOIN traffic_scheduleblock sb ON sb.ID_TrafficSchedule=ca.ID_TrafficSchedule
                        JOIN traffic_block bl ON bl.ID_TrafficBlock=sb.ID_TrafficBlock
                        WHERE ca.Cod_User=%s AND ca.Date=%s)""",
                    (s["sched"], cu, d, cu, d, s["sched"], cu, d, cu, d, cu, d),
                )
                miss, extra, nblk, mn, mx = cur.fetchone()
                if (
                    miss
                    or extra
                    or nblk != s["nblk"]
                    or int(mn) != DAY_START
                    or int(mx) - DAY_START != SPAN_24H
                ):
                    bad.append((d, miss, extra, nblk, mn, mx))
            print(f"  verified {len(targets)} day(s): {len(bad)} mismatch(es)")
            for b in bad[:10]:
                print(f"    MISMATCH {b}")
            if bad:
                ok = False

            cur.execute(
                """SELECT COUNT(DISTINCT ca.Date), MIN(ca.Date), MAX(ca.Date)
                           FROM Traffic_Calendar ca
                           JOIN traffic_scheduleblock sb ON sb.ID_TrafficSchedule=ca.ID_TrafficSchedule
                           WHERE ca.Cod_User=%s AND ca.Level=0""",
                (cu,),
            )
            nd, mn, mx = cur.fetchone()
            print(f"  {label} now programmed: {nd} day(s), {str(mn)[:10]} .. {str(mx)[:10]}")

        if not ok:
            print("\n[abort] verification failed — rolling back.")
            conn.rollback()
            return False
        if EXECUTE:
            conn.commit()
            print("\n[commit] grid written.")
        else:
            conn.rollback()
            print("\n[dry-run] all inserts verified, then ROLLED BACK. Nothing changed.")
        return True
    finally:
        try:
            conn.close()
        except Exception:
            pass


sys.exit(0 if main() else 1)
