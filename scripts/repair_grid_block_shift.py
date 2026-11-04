"""Find and repair program-grid blocks whose stored start offset slid off the previous
block's end (the one-pixel Weekly Schedule drag: +1438 frames = 47.98 s, seen on eight
days 2022-2026, Jenna's MMT 11/4/26 "12:00:47" report).

A slid block leaves the day's span and sum of durations at exactly 24:00:00 (one gap plus
one equal overlap), so span/sum checks never see it. Where the show was not fixed-time it
AIRED late by the slide.

Scope: Level-0 calendar days only. A candidate is a block whose offset differs from the
previous block's end by more than TOL frames and less than MAX_SLIDE frames, and whose
NEXT block starts exactly where this block would end once put back (so it is one block
that slid, not a re-laid day). Anything else is reported and left alone (the quarterly
Shop LC Saturday swaps and the DAL 2027 Saturday layout are whole-hour re-orderings, not
slides).

Repair per block, in one transaction:
  traffic_scheduleblock.Offset  = previous block's end
  trafficPalinse.offset        -= delta, for every booking inside the slid block
      (the nominal break a spot was booked into; TPALINSE.ORA, the air position, is never
      touched, so nothing moves on air and nothing is unscheduled)
In-transaction verify: block offset == previous end, next block still contiguous, every
rebased booking lands on one of the block's segment offsets, TPALINSE untouched. Restore
SQL is written to logs/ before the commit; a fresh connection reads both tables back.

Usage:
  uv run python3 scripts/repair_grid_block_shift.py [--from 2026-11-04] [--to 2027-12-31]
                                                    [--markets 8,9] [--apply]
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from browser_automation.etere_direct_client import connect  # noqa: E402

FPS = 29.97
TOL = 3  # frames; the weekend 11:45 block is 2 frames long on every market (benign)
MAX_SLIDE = 5 * 60 * 30  # frames; a slide is seconds, a re-laid day is whole half hours
LOG_DIR = Path(__file__).resolve().parents[1] / "logs"


def hhmmss(frames: int) -> str:
    s = frames / FPS
    h = int(s // 3600)
    m = int(s % 3600 // 60)
    return f"{h:02d}:{m:02d}:{s % 60:05.2f}"


def load_days(cur, d_from: date, d_to: date, markets: list[int] | None):
    """{(date, market): [block rows in offset order]} for Level-0 calendar days."""
    sql = """
      SELECT tc.ID_TrafficCalendar cal, CONVERT(varchar(10), tc.Date, 120) d, tc.Cod_User mkt,
             tsb.ID_TrafficScheduleBlock sbid, tsb.Offset ofs, tb.ID_TrafficBlock bid,
             tb.Duration dur, tb.Name bname
      FROM Traffic_Calendar tc
      JOIN traffic_scheduleblock tsb ON tsb.ID_TrafficSchedule = tc.ID_TrafficSchedule
      JOIN traffic_block tb ON tb.ID_TrafficBlock = tsb.ID_TrafficBlock
      WHERE tc.Level = 0 AND tc.Date BETWEEN %s AND %s
    """
    params: list = [d_from.isoformat(), d_to.isoformat()]
    if markets:
        sql += " AND tc.Cod_User IN (" + ",".join("%s" for _ in markets) + ")"
        params += markets
    sql += " ORDER BY tc.Cod_User, tc.Date, tsb.Offset"
    cur.execute(sql, tuple(params))
    days: dict = {}
    for r in cur.fetchall():
        days.setdefault((r["d"], r["mkt"]), []).append(r)
    return days


def find_slides(days: dict):
    """Returns (fixable, other). fixable: dicts describing one slid block each."""
    fixable, other = [], []
    for (d, mkt), blocks in days.items():
        for i in range(1, len(blocks)):
            prev, cur_b = blocks[i - 1], blocks[i]
            prev_end = prev["ofs"] + prev["dur"]
            delta = cur_b["ofs"] - prev_end
            if abs(delta) <= TOL:
                continue
            row = {
                "date": d,
                "market": mkt,
                "sbid": cur_b["sbid"],
                "block": cur_b["bid"],
                "name": cur_b["bname"],
                "dur": cur_b["dur"],
                "old": cur_b["ofs"],
                "new": prev_end,
                "delta": delta,
            }
            nxt = blocks[i + 1] if i + 1 < len(blocks) else None
            contiguous_after_fix = nxt is None or nxt["ofs"] == prev_end + cur_b["dur"]
            if abs(delta) < MAX_SLIDE and contiguous_after_fix:
                fixable.append(row)
            else:
                row["why"] = (
                    "slide larger than 5 min"
                    if abs(delta) >= MAX_SLIDE
                    else "next block not contiguous after fix"
                )
                other.append(row)
    return fixable, other


def bookings_in_block(cur, fix: dict):
    cur.execute(
        """
        SELECT tp.id_trafficPalinse tpid, tp.id_tpalinse tp_id, tp.offset ofs, tp.ID_ContrattiRighe line
        FROM trafficPalinse tp
        WHERE tp.Cod_User = %s AND tp.Date = %s AND tp.offset BETWEEN %s AND %s
        ORDER BY tp.offset
        """,
        (fix["market"], fix["date"], fix["old"], fix["old"] + fix["dur"] - 1),
    )
    return cur.fetchall()


def segment_offsets(cur, block_id: int) -> set[int]:
    cur.execute("SELECT Offset FROM traffic_segment WHERE ID_TrafficBlock = %s", (block_id,))
    return {r["Offset"] for r in cur.fetchall()}


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--from", dest="d_from", default=date.today().isoformat())
    ap.add_argument("--to", dest="d_to", default="2027-12-31")
    ap.add_argument("--markets", default="", help="comma-separated Cod_User list; default all")
    ap.add_argument(
        "--apply", action="store_true", help="write; default is a dry run that rolls back"
    )
    args = ap.parse_args()
    d_from, d_to = date.fromisoformat(args.d_from), date.fromisoformat(args.d_to)
    markets = [int(x) for x in args.markets.split(",") if x.strip()] or None

    with connect() as conn:
        cur = conn.cursor(as_dict=True)
        days = load_days(cur, d_from, d_to, markets)
        fixable, other = find_slides(days)
        print(
            f"scanned {len(days)} station-days {d_from}..{d_to}"
            + (f" markets {markets}" if markets else "")
        )
        for o in other:
            print(
                f"  LEFT ALONE {o['date']} mkt {o['market']} {hhmmss(o['old'])} {o['name']!r}"
                f" delta {o['delta']:+d} fr ({o['why']})"
            )
        if not fixable:
            print("no slid blocks found")
            return 0

        plan = []
        for f in fixable:
            rows = bookings_in_block(cur, f)
            segs = segment_offsets(cur, f["block"])
            bad = [r for r in rows if (r["ofs"] - f["delta"] - f["new"]) not in segs]
            f["bookings"], f["bad"] = rows, bad
            plan.append(f)
            print(
                f"  FIX {f['date']} mkt {f['market']} sb {f['sbid']} {f['name']!r}: "
                f"{hhmmss(f['old'])} -> {hhmmss(f['new'])} (delta {f['delta']:+d} fr = {f['delta'] / FPS:+.2f} s); "
                f"{len(rows)} booking(s) to rebase"
                + (f", {len(bad)} NOT on a segment" if bad else "")
            )
        if any(f["bad"] for f in plan):
            print("ABORT: a rebased booking would not land on a segment boundary; nothing written")
            return 2

        # Restore file first.
        LOG_DIR.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        restore = LOG_DIR / f"grid-block-shift-restore-{stamp}.sql"
        lines = [f"-- restore for repair_grid_block_shift.py run {stamp}"]
        for f in plan:
            lines.append(
                f"UPDATE traffic_scheduleblock SET Offset = {f['old']} WHERE ID_TrafficScheduleBlock = {f['sbid']};"
            )
            for r in f["bookings"]:
                lines.append(
                    f"UPDATE trafficPalinse SET offset = {r['ofs']} WHERE id_trafficPalinse = {r['tpid']};"
                )
        restore.write_text("\n".join(lines) + "\n")
        print(f"restore SQL: {restore}")

        # Write inside one transaction, verify, then commit or roll back.
        tpal_before = {}
        for f in plan:
            cur.execute(
                "SELECT ID_TPALINSE id, ORA FROM TPALINSE WHERE COD_USER = %s AND DATA = %s AND LIVELLO = 0",
                (f["market"], f["date"]),
            )
            tpal_before[(f["date"], f["market"])] = {r["id"]: r["ORA"] for r in cur.fetchall()}

        for f in plan:
            cur.execute(
                "UPDATE traffic_scheduleblock SET Offset = %s WHERE ID_TrafficScheduleBlock = %s AND Offset = %s",
                (f["new"], f["sbid"], f["old"]),
            )
            if cur.rowcount != 1:
                raise RuntimeError(f"block {f['sbid']} changed under us (rowcount {cur.rowcount})")
            for r in f["bookings"]:
                cur.execute(
                    "UPDATE trafficPalinse SET offset = %s WHERE id_trafficPalinse = %s AND offset = %s",
                    (r["ofs"] - f["delta"], r["tpid"], r["ofs"]),
                )
                if cur.rowcount != 1:
                    raise RuntimeError(f"booking {r['tpid']} changed under us")

        # In-transaction verify.
        days2 = load_days(cur, d_from, d_to, markets)
        fix2, _ = find_slides(days2)
        if fix2:
            raise RuntimeError(f"verify: {len(fix2)} slid block(s) remain after the write")
        for f in plan:
            blocks = days2[(f["date"], f["market"])]
            idx = next(i for i, b in enumerate(blocks) if b["sbid"] == f["sbid"])
            if blocks[idx]["ofs"] != f["new"]:
                raise RuntimeError(
                    f"verify: block {f['sbid']} offset is {blocks[idx]['ofs']}, wanted {f['new']}"
                )
            if idx + 1 < len(blocks) and blocks[idx + 1]["ofs"] != f["new"] + f["dur"]:
                raise RuntimeError(f"verify: block after {f['sbid']} is no longer contiguous")
            segs = segment_offsets(cur, f["block"])
            for r in f["bookings"]:
                cur.execute(
                    "SELECT offset FROM trafficPalinse WHERE id_trafficPalinse = %s", (r["tpid"],)
                )
                got = cur.fetchone()["offset"]
                if got - f["new"] not in segs:
                    raise RuntimeError(
                        f"verify: booking {r['tpid']} offset {got} is not on a segment"
                    )
            cur.execute(
                "SELECT ID_TPALINSE id, ORA FROM TPALINSE WHERE COD_USER = %s AND DATA = %s AND LIVELLO = 0",
                (f["market"], f["date"]),
            )
            after = {r["id"]: r["ORA"] for r in cur.fetchall()}
            if after != tpal_before[(f["date"], f["market"])]:
                raise RuntimeError(f"verify: TPALINSE changed for {f['date']} mkt {f['market']}")
        print(
            f"in-transaction verify OK: {len(plan)} block(s), {sum(len(f['bookings']) for f in plan)} booking(s)"
        )

        if not args.apply:
            conn.rollback()
            print("DRY RUN: rolled back (re-run with --apply)")
            restore.unlink()
            return 0
        conn.commit()
        print("COMMITTED")

    # Fresh-connection readback.
    with connect() as conn:
        cur = conn.cursor(as_dict=True)
        days3 = load_days(cur, d_from, d_to, markets)
        fix3, _ = find_slides(days3)
        for f in plan:
            cur.execute(
                "SELECT Offset FROM traffic_scheduleblock WHERE ID_TrafficScheduleBlock = %s",
                (f["sbid"],),
            )
            got = cur.fetchone()["Offset"]
            print(
                f"  readback {f['date']} mkt {f['market']} sb {f['sbid']}: {hhmmss(got)} {'OK' if got == f['new'] else 'MISMATCH'}"
            )
        print(
            f"readback: {len(fix3)} slid block(s) remain in range"
            + (" -- OK" if not fix3 else " -- CHECK")
        )
        return 0 if not fix3 else 1


if __name__ == "__main__":
    sys.exit(main())
