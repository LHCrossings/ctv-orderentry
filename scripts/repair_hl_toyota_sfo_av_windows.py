"""Trim the Added Value lines on H/L Toyota SFO 4Q26 (3137, 3138, 3139) to the paid span.

Entered 9/29 with AV across the untrimmed IO flight; the traffic instructions do not allow
airing before the first paid day. New rule (Lee 9/30, added_value.paid_span): AV runs from
the first to the last day a paid line can air, one spot per day.

  3137 line 84880  9/28-11/01 35 -> 10/03-11/01 30   (drop 9/30 x2, 10/1, 10/2, one of two on 10/3)
  3138 line 84886 11/02-12/06 35 -> 11/03-12/06 34   (drop 11/2)
  3139 line 84892 11/30-01/03 35 -> 12/07-01/03 28   (drop 11/30-12/6; header start 11/30 -> 12/07)

Unschedule = DELETE trafficPalinse + TPALINSE (7/14 ghost lesson), only Idle unaired rows.
Dry run by default (rollback). --apply commits and re-reads from a fresh connection.
"""

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from browser_automation.etere_direct_client import connect  # noqa: E402

PLAN = [
    # contract, av line, new_from, new_to, header_start_after
    (3137, 84880, date(2026, 10, 3), date(2026, 11, 1), date(2026, 9, 28)),
    (3138, 84886, date(2026, 11, 3), date(2026, 12, 6), date(2026, 11, 2)),
    (3139, 84892, date(2026, 12, 7), date(2027, 1, 3), date(2026, 12, 7)),
]


def placed(cur, line_id):
    cur.execute(
        """SELECT tp.ID_TPALINSE tp_id, tpa.id_trafficPalinse tpa_id, tp.DATA d, tp.ORA, tp.STATUS, tp.LIVELLO
           FROM trafficPalinse tpa JOIN TPALINSE tp ON tp.ID_TPALINSE = tpa.id_tpalinse
           WHERE tpa.id_contrattirighe = %s ORDER BY tp.DATA, tp.ORA""",
        (line_id,),
    )
    return cur.fetchall()


def choose_deletes(rows, new_from, new_to, target):
    """Rows outside the window, then extras beyond one per day, until len == target."""
    out = [r for r in rows if not (new_from <= r["d"].date() <= new_to)]
    keep = [r for r in rows if new_from <= r["d"].date() <= new_to]
    seen = set()
    for r in keep:
        if len(rows) - len(out) <= target:
            break
        day = r["d"].date()
        if day in seen:
            out.append(r)
        seen.add(day)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    stamp = datetime.now().strftime("%Y%m%d")
    backup = (
        Path(__file__).resolve().parents[1] / "logs" / f"hl-toyota-sfo-av-trim-backup-{stamp}.json"
    )
    dump = {}

    with connect() as conn:
        cur = conn.cursor(as_dict=True)
        ok = True
        summary = []
        for cid, lid, nf, nt, hdr in PLAN:
            cur.execute(
                "SELECT N_PASSAGGI, CONVERT(VARCHAR(10),DATA_INIZIO,120) ds, CONVERT(VARCHAR(10),DATA_FINE,120) de, ID_BOOKINGCODE bc"
                " FROM CONTRATTIRIGHE WHERE ID_CONTRATTIRIGHE=%s AND ID_CONTRATTITESTATA=%s",
                (lid, cid),
            )
            line = cur.fetchone()
            assert line and line["bc"] == 1, (cid, lid, line)  # AV line only
            rows = placed(cur, lid)
            target = (nt - nf).days + 1
            dels = choose_deletes(rows, nf, nt, target)
            aired = [r for r in dels if r["STATUS"] not in ("I",) or r["LIVELLO"] != 0]
            if aired:
                print(
                    f"{cid}/{lid}: refusing — {len(aired)} row(s) to delete are not Idle/live: {aired[:3]}"
                )
                ok = False
                continue
            remaining = len(rows) - len(dels)
            print(
                f"{cid} AV {lid}: {line['ds']}..{line['de']} N={line['N_PASSAGGI']} placed={len(rows)}"
                f" -> {nf}..{nt} N={target}: delete {len(dels)} (dates {sorted({str(r['d'].date()) for r in dels})}), keep {remaining}"
            )
            if remaining != target:
                print(f"   ⚠ remaining {remaining} != target {target}")
                ok = False
                continue
            # backup rows
            ids = [r["tp_id"] for r in dels]
            if ids:
                cur.execute(
                    f"SELECT * FROM TPALINSE WHERE ID_TPALINSE IN ({','.join(map(str, ids))})"
                )
                dump[f"{cid}_tpalinse"] = [
                    {k: str(v) for k, v in r.items()} for r in cur.fetchall()
                ]
                cur.execute(
                    f"SELECT * FROM trafficPalinse WHERE id_tpalinse IN ({','.join(map(str, ids))})"
                )
                dump[f"{cid}_trafficpalinse"] = [
                    {k: str(v) for k, v in r.items()} for r in cur.fetchall()
                ]
            dump[f"{cid}_line_before"] = {k: str(v) for k, v in line.items()}
            # writes
            for r in dels:
                cur.execute(
                    "DELETE FROM trafficPalinse WHERE id_trafficPalinse=%s AND id_tpalinse=%s",
                    (r["tpa_id"], r["tp_id"]),
                )
                assert cur.rowcount == 1
                cur.execute(
                    "DELETE FROM TPALINSE WHERE ID_TPALINSE=%s AND LIVELLO=0 AND STATUS='I'",
                    (r["tp_id"],),
                )
                assert cur.rowcount == 1
            cur.execute(
                "UPDATE CONTRATTIRIGHE SET DATA_INIZIO=%s, DATESTART=%s, DATA_FINE=%s, DATEEND=%s, N_PASSAGGI=%s"
                " WHERE ID_CONTRATTIRIGHE=%s",  # both pairs: the form shows DATESTART/DATEEND
                (nf, nf, nt, nt, target, lid),
            )
            assert cur.rowcount == 1
            cur.execute(
                "SELECT MIN(DATA_INIZIO) mn, MAX(DATA_FINE) mx FROM CONTRATTIRIGHE WHERE ID_CONTRATTITESTATA=%s",
                (cid,),
            )
            mm = cur.fetchone()
            assert mm["mn"].date() == hdr, (cid, mm)
            cur.execute(
                "UPDATE CONTRATTITESTATA SET DATA_INIZIO=%s, DATA_TERMINE=%s WHERE ID_CONTRATTITESTATA=%s",
                (mm["mn"], mm["mx"], cid),
            )
            # readback in txn
            after = placed(cur, lid)
            days = {r["d"].date() for r in after}
            assert (
                len(after) == target
                and min(days) >= nf
                and max(days) <= nt
                and len(days) == len(after)
            ), (cid, len(after))
            summary.append((cid, lid, target, len(after)))

        if ok and dump:
            backup.write_text(json.dumps(dump, indent=1, default=str))
            print(f"backup: {backup}")
        if not (ok and args.apply):
            conn.rollback()
            print(
                "ROLLED BACK"
                + ("" if ok else " (checks failed)")
                + ("" if args.apply else " (dry run; pass --apply)")
            )
            return 0 if ok else 1
        conn.commit()
        print("COMMITTED", summary)

    with connect() as c2:
        cur = c2.cursor(as_dict=True)
        bad = 0
        for cid, lid, nf, nt, hdr in PLAN:
            cur.execute(
                "SELECT N_PASSAGGI n, CONVERT(VARCHAR(10),DATA_INIZIO,120) ds, CONVERT(VARCHAR(10),DATA_FINE,120) de FROM CONTRATTIRIGHE WHERE ID_CONTRATTIRIGHE=%s",
                (lid,),
            )
            ln = cur.fetchone()
            rows = placed(cur, lid)
            days = sorted({r["d"].date() for r in rows})
            cur.execute(
                "SELECT CONVERT(VARCHAR(10),DATA_INIZIO,120) ds FROM CONTRATTITESTATA WHERE ID_CONTRATTITESTATA=%s",
                (cid,),
            )
            h = cur.fetchone()["ds"]
            good = (
                ln["n"] == len(rows) == len(days)
                and days[0] >= nf
                and days[-1] <= nt
                and h == str(hdr)
            )
            bad += 0 if good else 1
            print(
                f"fresh: {cid} AV {lid} {ln['ds']}..{ln['de']} N={ln['n']} placed={len(rows)} days {days[0]}..{days[-1]} header {h} -> {'OK' if good else 'MISMATCH'}"
            )
        return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
