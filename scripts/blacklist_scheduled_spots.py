"""Send a contract's scheduled spots in a date range to the Etere blacklist.

Sibling of `delete_scheduled_spots.py` (same inputs, same preview) but it does what Etere's own
"blacklist" does, per the accounting rule N_PASSAGGI = trafficPalinse + Traffic_ScheduleList.PassageMiss:

  for every scheduled spot of the contract in the range (ordered by date, time):
    1. DELETE its trafficPalinse row (contract side)       — the delete utility stops here,
    2. DELETE its TPALINSE row (playlist side)              — which leaves ghosts that still AIR,
    3. Traffic_ScheduleList: INSERT the line's first blacklist row (BlackList=1, PassageMiss=1,
       Date/ToDate = the line's DATA_INIZIO/DATA_FINE), or PassageMiss + 1 on every later spot.

One transaction; every deleted row is written to logs/ as INSERT statements before the commit.
Requires --confirm to write. Dates in MM/DD/YYYY.

Usage:
    uv run python scripts/blacklist_scheduled_spots.py <contract_id> <from_date> <to_date>
    uv run python scripts/blacklist_scheduled_spots.py <contract_id> <from_date> <to_date> --confirm
"""

import os
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from browser_automation.etere_direct_client import connect  # noqa: E402

NOTES = "Blacklisted - Control Room"
OPERATOR = "ControlRoom"


def lit(v):
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float)) or type(v).__name__ == "Decimal":
        return str(v)
    if hasattr(v, "strftime"):
        return "'" + v.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + "'"
    if isinstance(v, (bytes, bytearray)):
        return "0x" + v.hex()
    return "N'" + str(v).replace("'", "''") + "'"


def parse_mdy(s: str) -> date:
    return datetime.strptime(s, "%m/%d/%Y").date()


def main():
    if len(sys.argv) < 4:
        print(
            "Usage: uv run python scripts/blacklist_scheduled_spots.py <contract_id> <from_date> <to_date> [--confirm]"
        )
        print("Dates in MM/DD/YYYY format.")
        sys.exit(1)

    contract_id = int(sys.argv[1])
    date_from = parse_mdy(sys.argv[2])
    date_to = parse_mdy(sys.argv[3])
    confirmed = "--confirm" in sys.argv
    rehearse = "--rehearse" in sys.argv  # run the whole write path, then ROLLBACK (test only)
    if rehearse:
        confirmed = True
    if date_to < date_from:
        print("[WARN] To Date is before From Date — nothing to do.")
        sys.exit(2)

    conn = connect()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT COD_CONTRATTO FROM CONTRATTITESTATA WHERE ID_CONTRATTITESTATA = %s",
            (contract_id,),
        )
        row = cur.fetchone()
        if not row:
            print(f"[WARN] Contract {contract_id} not found.")
            sys.exit(2)
        code = row[0]

        cur.execute(
            """
            SELECT t.id_trafficPalinse, t.id_tpalinse, t.ID_ContrattiRighe, t.Date, p.ORA, p.LIVELLO, p.STATUS
            FROM   trafficPalinse t
            JOIN   CONTRATTIRIGHE cr ON cr.ID_CONTRATTIRIGHE = t.ID_ContrattiRighe
            LEFT JOIN TPALINSE p ON p.ID_TPALINSE = t.id_tpalinse
            WHERE  cr.ID_CONTRATTITESTATA = %s AND t.Date >= %s AND t.Date < DATEADD(day, 1, %s)
            ORDER  BY t.Date, p.ORA, t.id_trafficPalinse
            """,
            (contract_id, date_from, date_to),
        )
        spots = cur.fetchall()
        if not spots:
            print(
                f"[INFO] No scheduled spots found for {code} ({contract_id}) between {date_from:%m/%d/%Y} and {date_to:%m/%d/%Y}."
            )
            return

        aired = [s for s in spots if s[6] in ("Q", "D", "A")]
        by_date: dict = {}
        for s in spots:
            by_date[s[3].date()] = by_date.get(s[3].date(), 0) + 1
        print(f"[PREVIEW] {code} ({contract_id}) — {date_from:%m/%d/%Y} to {date_to:%m/%d/%Y}")
        print(f"{'Date':>12}  {'Spots':>6}")
        print("-" * 22)
        for d in sorted(by_date):
            print(f"{d.strftime('%m/%d/%Y'):>12}  {by_date[d]:>6}")
        print("-" * 22)
        print(f"{'TOTAL':>12}  {len(spots):>6}")
        lines = sorted({s[2] for s in spots})
        print(
            f"[INFO] {len(lines)} contract line(s) affected; each gets its blacklist row inserted or PassageMiss raised."
        )
        if aired:
            print(
                f"[WARN] {len(aired)} of these spots already AIRED (status Q/D/A) — refusing. Narrow the date range."
            )
            sys.exit(2)
        print()
        if not confirmed:
            print(
                "[INFO] Dry run complete. Click Blacklist (or add --confirm) to send these spots to the blacklist."
            )
            return

        # ── backup ──────────────────────────────────────────────────────────────
        tp_ids = ",".join(str(s[0]) for s in spots)
        pl_ids = ",".join(str(s[1]) for s in spots if s[1])
        restore = [
            f"-- restore {len(spots)} blacklisted spot(s) of {code} ({contract_id}) {date_from:%m/%d}-{date_to:%m/%d}, generated {datetime.now():%Y-%m-%d %H:%M} — run as one batch",
            "BEGIN TRAN;",
        ]
        line_ids = ",".join(map(str, lines))
        cur.execute(
            f"SELECT ID_TrafficScheduleList, ID_ContrattiRighe, PassageMiss FROM Traffic_ScheduleList WHERE ID_ContrattiRighe IN ({line_ids}) AND BlackList > 0"
        )
        tsl_before = {r[1]: (r[0], int(r[2])) for r in cur.fetchall()}
        for lid, (tid, pm) in tsl_before.items():
            restore.append(
                f"UPDATE Traffic_ScheduleList SET PassageMiss={pm} WHERE ID_TrafficScheduleList={tid};"
            )
        restore.append(
            f"DELETE FROM Traffic_ScheduleList WHERE ID_ContrattiRighe IN ({line_ids}) AND BlackList > 0 AND Notes={lit(NOTES)} AND ID_TrafficScheduleList NOT IN ({','.join(str(t[0]) for t in tsl_before.values()) or '0'});"
        )
        for table, ids, idcol in (
            ("TPALINSE", pl_ids, "ID_TPALINSE"),
            ("trafficPalinse", tp_ids, "id_trafficPalinse"),
        ):
            if not ids:
                continue
            cur.execute(f"SELECT * FROM {table} WHERE {idcol} IN ({ids})")
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
            restore.append(f"SET IDENTITY_INSERT {table} ON;")
            for r in rows:
                restore.append(
                    f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join(lit(v) for v in r)});"
                )
            restore.append(f"SET IDENTITY_INSERT {table} OFF;")
        restore.append("COMMIT;")
        rpath = (
            Path("logs")
            / f"blacklist-{contract_id}-{date_from:%Y%m%d}-{date_to:%Y%m%d}-restore-{datetime.now():%Y%m%d-%H%M%S}.sql"
        )
        rpath.parent.mkdir(exist_ok=True)
        rpath.write_text("\n".join(restore) + "\n")
        print(f"[INFO] Backup written: {rpath}")

        # ── writes, one spot at a time, one transaction ─────────────────────────
        cur.execute(
            f"SELECT ID_CONTRATTIRIGHE, DATA_INIZIO, DATA_FINE FROM CONTRATTIRIGHE WHERE ID_CONTRATTIRIGHE IN ({line_ids})"
        )
        line_dates = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
        inserted, bumped = 0, 0
        for tp_id, pl_id, lid, _d, _ora, _lv, _st in spots:
            cur.execute("DELETE FROM trafficPalinse WHERE id_trafficPalinse = %s", (tp_id,))
            assert cur.rowcount == 1, f"trafficPalinse {tp_id} vanished mid-run"
            if pl_id:
                cur.execute("DELETE FROM TPALINSE WHERE ID_TPALINSE = %s", (pl_id,))
            cur.execute(
                "SELECT ID_TrafficScheduleList FROM Traffic_ScheduleList WHERE ID_ContrattiRighe = %s AND BlackList > 0",
                (lid,),
            )
            if cur.fetchone() is None:
                d0, d1 = line_dates[lid]
                cur.execute(
                    """
                    INSERT INTO Traffic_ScheduleList (
                        ID_ContrattiRighe, BlackList, PassageMiss, ID_TRAFFICPALINSE, Date, ToDate,
                        Notes, Operator, ID_FILMATI, ID_FILMATI_TAIL, ID_FILMATI_MIDDLE, ID_FATTURAEMITTENTE, Split
                    ) VALUES (%s, 1, 1, %s, %s, %s, %s, %s, -1, -1, -1, 0, 0)
                    """,
                    (lid, tp_id, d0, d1, NOTES, OPERATOR),
                )
                inserted += 1
            else:
                cur.execute(
                    "UPDATE Traffic_ScheduleList SET PassageMiss = PassageMiss + 1 WHERE ID_ContrattiRighe = %s AND BlackList > 0",
                    (lid,),
                )
                bumped += 1

        # ── verify inside the transaction ───────────────────────────────────────
        cur.execute(
            """
            SELECT COUNT(*) FROM trafficPalinse t JOIN CONTRATTIRIGHE cr ON cr.ID_CONTRATTIRIGHE = t.ID_ContrattiRighe
            WHERE cr.ID_CONTRATTITESTATA = %s AND t.Date >= %s AND t.Date < DATEADD(day, 1, %s)
            """,
            (contract_id, date_from, date_to),
        )
        left = cur.fetchone()[0]
        if pl_ids:
            cur.execute(f"SELECT COUNT(*) FROM TPALINSE WHERE ID_TPALINSE IN ({pl_ids})")
            ghosts = cur.fetchone()[0]
        else:
            ghosts = 0
        cur.execute(
            f"SELECT ID_ContrattiRighe, SUM(PassageMiss) FROM Traffic_ScheduleList WHERE ID_ContrattiRighe IN ({line_ids}) AND BlackList > 0 GROUP BY ID_ContrattiRighe"
        )
        tsl_after = {r[0]: int(r[1]) for r in cur.fetchall()}
        removed_per_line: dict = {}
        for s in spots:
            removed_per_line[s[2]] = removed_per_line.get(s[2], 0) + 1
        tsl_ok = all(
            tsl_after.get(lid, 0) == tsl_before.get(lid, (0, 0))[1] + n
            for lid, n in removed_per_line.items()
        )
        if left or ghosts or not tsl_ok:
            conn.rollback()
            print(
                f"[WARN] Verify failed (left={left}, playlist rows left={ghosts}, blacklist counts ok={tsl_ok}) — rolled back, nothing changed."
            )
            sys.exit(2)
        if rehearse:
            conn.rollback()
            print(
                f"[REHEARSE] write path verified in-transaction for {len(spots)} spot(s) "
                f"({inserted} insert(s), {bumped} increment(s)) — rolled back, nothing changed."
            )
            return
        conn.commit()
        print(
            f"[DONE] {len(spots)} spot(s) of {code} sent to the blacklist: contract + playlist rows removed, "
            f"{inserted} blacklist row(s) inserted, {bumped} PassageMiss increment(s)."
        )
        # accounting readback from a fresh connection: N_PASSAGGI = placed + blacklisted
        c2 = connect()
        k = c2.cursor()
        k.execute(
            f"""
            SELECT cr.ID_CONTRATTIRIGHE, cr.N_PASSAGGI,
                   (SELECT COUNT(*) FROM trafficPalinse t WHERE t.ID_ContrattiRighe = cr.ID_CONTRATTIRIGHE),
                   ISNULL((SELECT SUM(PassageMiss) FROM Traffic_ScheduleList s WHERE s.ID_ContrattiRighe = cr.ID_CONTRATTIRIGHE AND s.BlackList > 0), 0)
            FROM CONTRATTIRIGHE cr WHERE cr.ID_CONTRATTIRIGHE IN ({line_ids}) ORDER BY 1
            """
        )
        for lid, n, placed, missed in k.fetchall():
            flag = "" if placed + missed <= n else "  [WARN] placed + blacklisted exceeds ordered"
            print(f"[INFO] line {lid}: ordered {n}, placed {placed}, blacklisted {missed}{flag}")
        c2.close()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
