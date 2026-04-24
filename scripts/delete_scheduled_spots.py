"""
Delete scheduled spots from trafficPalinse for a contract within a date range.

Shows a preview (count by date) before committing. Requires --confirm to actually delete.

Usage:
    uv run python scripts/delete_scheduled_spots.py <contract_id> <from_date> <to_date>
    uv run python scripts/delete_scheduled_spots.py <contract_id> <from_date> <to_date> --confirm

Dates in MM/DD/YYYY format.

Example (dry run):
    uv run python scripts/delete_scheduled_spots.py 2551 06/01/2026 06/21/2026

Example (delete):
    uv run python scripts/delete_scheduled_spots.py 2551 06/01/2026 06/21/2026 --confirm
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from browser_automation.etere_direct_client import connect


def main():
    if len(sys.argv) < 4:
        print("Usage: uv run python scripts/delete_scheduled_spots.py <contract_id> <from_date> <to_date> [--confirm]")
        print("Dates in MM/DD/YYYY format.")
        sys.exit(1)

    contract_id = int(sys.argv[1])
    date_from   = sys.argv[2]
    date_to     = sys.argv[3]
    confirmed   = "--confirm" in sys.argv

    with connect() as conn:
        cursor = conn.cursor()

        # Preview: count by date
        cursor.execute("""
            SELECT CONVERT(varchar(10), t.Date, 101) AS AirDate, COUNT(*) AS Spots
            FROM   trafficPalinse t
            JOIN   CONTRATTIRIGHE cr ON cr.ID_CONTRATTIRIGHE = t.ID_ContrattiRighe
            WHERE  cr.ID_CONTRATTITESTATA = ?
              AND  t.Date >= ?
              AND  t.Date < DATEADD(day, 1, CONVERT(datetime, ?, 101))
            GROUP  BY CONVERT(varchar(10), t.Date, 101)
            ORDER  BY AirDate
        """, [contract_id, date_from, date_to])
        rows = cursor.fetchall()

        if not rows:
            print(f"[INFO] No scheduled spots found for contract {contract_id} between {date_from} and {date_to}.")
            return

        total = sum(r[1] for r in rows)
        print(f"[PREVIEW] Contract {contract_id} — {date_from} to {date_to}")
        print(f"{'Date':>12}  {'Spots':>6}")
        print("-" * 22)
        for air_date, count in rows:
            print(f"{air_date:>12}  {count:>6}")
        print("-" * 22)
        print(f"{'TOTAL':>12}  {total:>6}")
        print()

        if not confirmed:
            print("[INFO] Dry run complete. Add --confirm to delete these rows.")
            return

        # Collect line IDs for this contract then delete by ID list
        cursor.execute(
            "SELECT ID_CONTRATTIRIGHE FROM CONTRATTIRIGHE WHERE ID_CONTRATTITESTATA = ?",
            [contract_id]
        )
        line_ids = [r[0] for r in cursor.fetchall()]
        if not line_ids:
            print("[INFO] No lines found for this contract.")
            return

        placeholders = ",".join("?" * len(line_ids))
        cursor.execute(f"""
            DELETE FROM trafficPalinse
            WHERE  ID_ContrattiRighe IN ({placeholders})
              AND  Date >= ?
              AND  Date < DATEADD(day, 1, CONVERT(datetime, ?, 101))
        """, [*line_ids, date_from, date_to])

        deleted = cursor.rowcount
        conn.commit()
        print(f"[DONE] Deleted {deleted} scheduled spot(s) from contract {contract_id} ({date_from} to {date_to}).")


if __name__ == "__main__":
    main()
