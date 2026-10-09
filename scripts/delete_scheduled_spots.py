"""
Delete scheduled spots for a contract within a date range — from BOTH tables.

A placed spot is two rows: `trafficPalinse` (contract side) and `TPALINSE` (playlist side).
Until 2026-10-09 this script deleted the contract side only, which left the playlist rows in
place as ghosts that still air (Lexus 2740/2742/2747 weekend pull: 28 ghosts). It now removes
both rows for every spot, the way Etere's own unschedule does. No blacklist accounting is
written — use `blacklist_scheduled_spots.py` (/scripts/blacklist-spots) when the spots should
count as blacklisted (ordered = placed + blacklisted).

Shows a preview (count by date) before committing. Requires --confirm to actually delete.
Refuses spots that already aired (status Q/D/A) — those are the as-run record.
Every deleted row is backed up to logs/ as INSERT statements before the commit.

Usage:
    uv run python scripts/delete_scheduled_spots.py <contract_id> <from_date> <to_date>
    uv run python scripts/delete_scheduled_spots.py <contract_id> <from_date> <to_date> --confirm

Dates in MM/DD/YYYY format.
"""

import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from browser_automation.etere_direct_client import connect  # noqa: E402


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


def main():
    if len(sys.argv) < 4:
        print(
            "Usage: uv run python scripts/delete_scheduled_spots.py <contract_id> <from_date> <to_date> [--confirm]"
        )
        print("Dates in MM/DD/YYYY format.")
        sys.exit(1)

    contract_id = int(sys.argv[1])
    date_from = datetime.strptime(sys.argv[2], "%m/%d/%Y").date()
    date_to = datetime.strptime(sys.argv[3], "%m/%d/%Y").date()
    confirmed = "--confirm" in sys.argv
    rehearse = "--rehearse" in sys.argv  # whole write path, then ROLLBACK (test only)
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
            SELECT t.id_trafficPalinse, t.id_tpalinse, t.Date, p.STATUS
            FROM   trafficPalinse t
            JOIN   CONTRATTIRIGHE cr ON cr.ID_CONTRATTIRIGHE = t.ID_ContrattiRighe
            LEFT JOIN TPALINSE p ON p.ID_TPALINSE = t.id_tpalinse
            WHERE  cr.ID_CONTRATTITESTATA = %s AND t.Date >= %s AND t.Date < DATEADD(day, 1, %s)
            ORDER  BY t.Date, t.id_trafficPalinse
            """,
            (contract_id, date_from, date_to),
        )
        spots = cur.fetchall()
        if not spots:
            print(
                f"[INFO] No scheduled spots found for {code} ({contract_id}) between {date_from:%m/%d/%Y} and {date_to:%m/%d/%Y}."
            )
            return

        by_date: dict = {}
        for s in spots:
            by_date[s[2].date()] = by_date.get(s[2].date(), 0) + 1
        print(f"[PREVIEW] {code} ({contract_id}) — {date_from:%m/%d/%Y} to {date_to:%m/%d/%Y}")
        print(f"{'Date':>12}  {'Spots':>6}")
        print("-" * 22)
        for d in sorted(by_date):
            print(f"{d.strftime('%m/%d/%Y'):>12}  {by_date[d]:>6}")
        print("-" * 22)
        print(f"{'TOTAL':>12}  {len(spots):>6}")
        aired = [s for s in spots if s[3] in ("Q", "D", "A")]
        if aired:
            print(
                f"[WARN] {len(aired)} of these spots already AIRED (status Q/D/A) — refusing. Narrow the date range."
            )
            sys.exit(2)
        print()
        if not confirmed:
            print(
                "[INFO] Dry run complete. Add --confirm to delete these rows (contract AND playlist)."
            )
            return

        # ── backup ──────────────────────────────────────────────────────────────
        tp_ids = ",".join(str(s[0]) for s in spots)
        pl_ids = ",".join(str(s[1]) for s in spots if s[1])
        restore = [
            f"-- restore {len(spots)} deleted spot(s) of {code} ({contract_id}) {date_from:%m/%d}-{date_to:%m/%d}, generated {datetime.now():%Y-%m-%d %H:%M} — run as one batch",
            "BEGIN TRAN;",
        ]
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
            / f"delete-spots-{contract_id}-{date_from:%Y%m%d}-{date_to:%Y%m%d}-restore-{datetime.now():%Y%m%d-%H%M%S}.sql"
        )
        rpath.parent.mkdir(exist_ok=True)
        rpath.write_text("\n".join(restore) + "\n")
        print(f"[INFO] Backup written: {rpath}")

        # ── delete both sides, one transaction ──────────────────────────────────
        cur.execute(f"DELETE FROM trafficPalinse WHERE id_trafficPalinse IN ({tp_ids})")
        deleted_tp = cur.rowcount
        deleted_pl = 0
        if pl_ids:
            cur.execute(f"DELETE FROM TPALINSE WHERE ID_TPALINSE IN ({pl_ids})")
            deleted_pl = cur.rowcount

        cur.execute(
            """
            SELECT COUNT(*) FROM trafficPalinse t JOIN CONTRATTIRIGHE cr ON cr.ID_CONTRATTIRIGHE = t.ID_ContrattiRighe
            WHERE cr.ID_CONTRATTITESTATA = %s AND t.Date >= %s AND t.Date < DATEADD(day, 1, %s)
            """,
            (contract_id, date_from, date_to),
        )
        left = cur.fetchone()[0]
        ghosts = 0
        if pl_ids:
            cur.execute(f"SELECT COUNT(*) FROM TPALINSE WHERE ID_TPALINSE IN ({pl_ids})")
            ghosts = cur.fetchone()[0]
        if deleted_tp != len(spots) or left or ghosts:
            conn.rollback()
            print(
                f"[WARN] Verify failed (deleted {deleted_tp}/{len(spots)}, left={left}, playlist rows left={ghosts}) — rolled back, nothing changed."
            )
            sys.exit(2)
        if rehearse:
            conn.rollback()
            print(
                f"[REHEARSE] write path verified in-transaction: {deleted_tp} contract + {deleted_pl} playlist row(s) — rolled back, nothing changed."
            )
            return
        conn.commit()
        print(
            f"[DONE] Deleted {deleted_tp} scheduled spot(s) from {code} ({date_from:%m/%d/%Y} to {date_to:%m/%d/%Y}): "
            f"{deleted_tp} contract row(s) and {deleted_pl} playlist row(s). No blacklist accounting written."
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
